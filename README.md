# AI Request Automation

Dos workflows de n8n y un servicio Python pequeño para recibir solicitudes, preparar borradores con IA y crear una issue de GitHub tras una aprobación explícita.

**Proveedor actual:** modelos gratuitos mediante OpenCode CLI 2.2.5. No se usan claves de OpenAI ni hay cambio automático a proveedores de pago. La disponibilidad gratuita está sujeta a cupos y puede cambiar.

## Estado verificado

- Pruebas del servicio con SQLite real, incluyendo solicitudes y aprobaciones concurrentes.
- Los dos workflows se importaron, publicaron y ejecutaron dentro de n8n 2.47.5 contra fixtures explícitas de IA y GitHub.
- Se verificaron éxito, duplicados, rechazo, error de análisis y creación remota seguida de una respuesta incierta.
- Las llamadas reales a dos modelos gratuitos devolvieron límite de uso (`provider.free-limit`, 429). **La calidad de clasificación real aún no está evaluada.** Ver [estado y pasos pendientes](evaluation/STATUS.md).
- No se ha creado una issue real en GitHub. Las pruebas no publican contenido en cuentas externas.

## Preparar el entorno

Requiere Docker Compose. Para desarrollo y pruebas locales, Python 3.13. El contenedor instala la CLI de OpenCode; para ejecutar el proveedor desde el host necesitas `opencode` 2.2.5 en el PATH.

```sh
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/init_env.py
```

`init_env.py` genera credenciales locales aleatorias sin mostrarlas ni sobrescribir `.env`. El archivo está excluido de Git. Configura `GITHUB_REPOSITORY=propietario/repositorio` cuando decidas dónde publicar las issues. Déjalo vacío mientras solo explores el análisis: aprobar devolverá `503` sin consumir el borrador.

```sh
docker compose up -d --build api
docker compose run --rm --no-deps n8n import:workflow --separate --input=/workflows
docker compose up -d n8n
```

La importación se realiza antes de iniciar n8n para evitar migraciones concurrentes de su base local. Si ya está ejecutándose, detén únicamente ese servicio antes de importar.

- API: `http://127.0.0.1:8001/docs`
- n8n: `http://127.0.0.1:5678`

En n8n, crea la cuenta local que solicita el primer arranque. Configura una credencial GitHub API con el nombre que prefieras, servidor `https://api.github.com` y acceso para crear issues en el repositorio elegido. Selecciónala en el nodo **Create issue** del workflow **AI Requests - Review**. La referencia exportada `Automation GitHub` es un marcador de configuración, no una credencial incluida.

Después de revisar el destino y la credencial, publica ambos workflows desde n8n. No importes la credencial de `tests/github-fixture.json` en este entorno: pertenece exclusivamente al servidor simulado.

Para detener los servicios conservando sus bases:

```sh
docker compose stop
```

## Recorrido y comandos

Los ejemplos asumen que `AUTOMATION_API_TOKEN` y `REVIEWER_TOKEN` están exportadas con los valores de tu `.env`. No pongas claves reales en archivos versionados.

### 1. Recibir y analizar

```sh
curl -i -X POST http://127.0.0.1:5678/webhook/automation/requests \
  -H "X-API-Key: $AUTOMATION_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"solicitud-001","subject":"Error al descargar reporte","message":"Desde ayer no puedo descargar el reporte mensual."}'
```

El primer análisis válido devuelve `201` con estado `pending_review`, categoría (`incidente`, `solicitud` o `consulta`), título, resumen e información pendiente. La salida del modelo se valida con Pydantic antes de guardarla como borrador.

Repetir el mismo identificador y los mismos datos devuelve el estado existente (`200` o `202` si aún se analiza) sin volver a llamar al modelo. Cambiar los datos con el mismo identificador devuelve `409`. Se recortan espacios iniciales y finales antes de comparar; los campos desconocidos se rechazan.

### 2. Consultar el borrador

```sh
curl http://127.0.0.1:8001/requests/solicitud-001 \
  -H "X-API-Key: $AUTOMATION_API_TOKEN"
```

La consulta incluye contenido original, borrador, estado, error y métricas del último análisis. No expone el token interno de publicación.

### 3. Aprobar o rechazar

```sh
curl -i -X POST http://127.0.0.1:5678/webhook/automation/review \
  -H "X-API-Key: $AUTOMATION_API_TOKEN" \
  -H "X-Reviewer-Key: $REVIEWER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"solicitud-001","decision":"approve"}'
```

Usa `reject` para rechazar sin publicar. La aprobación utiliza exactamente el borrador almacenado y el repositorio configurado en Python. Ni la solicitud ni el modelo pueden cambiar el destino. Las aprobaciones repetidas consultan el resultado o informan que la publicación está en curso; no conceden otro intento.

## Estados y fallos

| Estado | Significado |
| --- | --- |
| `analyzing` | Un análisis posee la solicitud; los duplicados no ejecutan otro |
| `analysis_failed` | El análisis falló y conserva un código de error |
| `pending_review` | Borrador válido, todavía no aprobado |
| `rejected` | Decisión final sin publicación |
| `publishing` | Se reservó un único intento de publicación |
| `published` | Se registró el número y enlace de la issue |
| `needs_reconciliation` | GitHub falló o la respuesta de creación es incierta |

Fallos de análisis devuelven `502`; entrada inválida, `422`; credencial de API inválida, `401`; credencial de revisor inválida, `403`; solicitud inexistente, `404`; transición incompatible, `409`.

El cupo gratuito agotado se registra como `model_rate_limited`, sin reintento ni cambio automático de modelo. También se controlan CLI ausente, timeout, fallo del proveedor y salida inválida. OpenCode tiene 60 segundos por ejecución; al vencer se termina su grupo de procesos. Los nodos HTTP tienen un margen mayor para recibir ese resultado.

Reintentar un análisis fallido requiere una acción explícita autenticada:

```sh
curl -i -X POST http://127.0.0.1:8001/requests/solicitud-001/retry-analysis \
  -H "X-API-Key: $AUTOMATION_API_TOKEN" -H "X-Reviewer-Key: $REVIEWER_TOKEN"
```

Un duplicado de recepción no reintenta el análisis. Si el proceso muere abruptamente durante `analyzing`, el registro requiere diagnóstico manual; no implementamos leases ni recuperación automática.

### Publicación incierta

Python reserva el intento en una transacción SQLite antes de que n8n invoque GitHub. Si GitHub falla, n8n registra `needs_reconciliation`. Si se interrumpe antes de confirmar, puede permanecer en `publishing`. En ambos casos otra aprobación **no vuelve a crear la issue**.

Cada issue lleva el marcador `automation-request:ID`. Un operador debe comprobar el repositorio y el marcador. Si encuentra la issue existente, registra su número:

```sh
curl -i -X POST http://127.0.0.1:8001/requests/solicitud-001/reconcile \
  -H "X-API-Key: $AUTOMATION_API_TOKEN" -H "X-Reviewer-Key: $REVIEWER_TOKEN" \
  -H 'Content-Type: application/json' -d '{"issue_number":123}'
```

Este endpoint no crea ni verifica una issue remota: registra la comprobación del operador. No permite publicar un borrador sin aprobación. Si no existe una issue, no se libera automáticamente otro intento. No uses el reintento manual del nodo GitHub ni reproduzcas ejecuciones desde ese nodo; vuelve a entrar por el webhook de revisión.

La garantía es un único permiso de creación por solicitud a través de este flujo, no una transacción distribuida de ejecución exactamente una vez. Editores de n8n y poseedores de las credenciales son operadores de confianza.

## Diseño y proveedor

- [Workflows](workflows/): recepción y revisión. No usan nodos de agentes ni Code; el nodo GitHub realiza la creación.
- [API](app/main.py): validación, autorización y coordinación de operaciones.
- [SQLite](app/store.py): restricciones de identificador y transiciones con `BEGIN IMMEDIATE`. Las llamadas al modelo y a GitHub ocurren fuera de las transacciones.
- [OpenCode](app/ai.py): CLI oficial en instancia privada, directorios temporales aislados y herramientas deshabilitadas. Se permite un solo paso del modelo y se valida JSON estricto al terminar. El modelo nunca recibe tokens de la aplicación, acceso al repositorio o herramientas de publicación.

Se admiten explícitamente GLM-5.3-Flash Free y Nemotron 3.5 Lightning Free mediante `OPENCODE_MODEL`. No se sustituye un modelo por otro automáticamente. El esquema está en el prompt; esta vía no garantiza el formato desde el proveedor, por eso la validación y las pruebas de salida inválida son necesarias. Los modelos gratuitos y sus condiciones pueden cambiar; usa datos sintéticos de demostración y revisa las [condiciones actuales de OpenCode](https://opencode.ai/docs/console/models/).

Las métricas guardan modelo solicitado, versión del prompt, duración y tokens cuando OpenCode los informa. La ausencia de uso se representa como `null`. Los errores se guardan como códigos sin copiar excepciones que puedan contener credenciales. Las ejecuciones completas de n8n no se conservan en esta configuración local; los borradores y estados sí persisten en SQLite.

## Pruebas y evaluación

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke_workflows.py
```

La segunda comprobación levanta **n8n real** y servicios simulados de IA/GitHub en un proyecto Docker aislado, sin credenciales reales. Importa los dos workflows, los publica y los llama por HTTP. Prueba recepción, duplicados, autenticación, aprobación, rechazo, fallo de análisis y una creación remota cuya respuesta se pierde. Verifica que repetir la aprobación no crea otra issue. Al terminar elimina únicamente sus contenedores temporales.

El dataset incluye 30 solicitudes sintéticas: 10 de desarrollo y 20 de evaluación. Cuando el modelo gratuito tenga disponibilidad:

```sh
.venv/bin/python -m evaluation.run --split dev --output output/dev.json
.venv/bin/python -m evaluation.run --split eval --output output/evaluation.json
```

Las etiquetas esperadas no se envían al modelo. El reporte incluye aciertos por categoría, salidas válidas, errores, duración y uso informado. Los fallos cuentan en el denominador de exactitud general. La fidelidad del resumen y los datos inventados quedan para revisión humana. No hay un umbral de calidad declarado sin medición real.

## Límites

Demostración local con un único repositorio de destino y una única instancia del servicio. Sin edición de borradores, roles de usuario, adjuntos, RAG, agentes autónomos, reintentos automáticos de publicación o sistema de colas. SQLite serializa las escrituras y no está planteado como almacenamiento compartido entre máquinas. No se expone el entorno de desarrollo a Internet.

Referencias: [webhooks de n8n](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/), [nodo GitHub](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.github/), [CLI de OpenCode](https://opencode.ai/docs/cli/commands/) y [permisos](https://opencode.ai/docs/permissions/).
