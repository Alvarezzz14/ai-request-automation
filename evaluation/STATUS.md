# Estado de la evaluación

El 13 de noviembre de 2026 se verificó la integración real mediante OpenCode CLI 2.2.5 con solicitudes sintéticas. Tanto `opencode/glm-5.3-flash-free` como `opencode/nemotron-3.5-lightning-free` devolvieron `provider.free-limit` (HTTP 429).

No se completó una clasificación real ni la evaluación de calidad. No se publican porcentajes de acierto derivados de respuestas simuladas. La validación sintáctica, las métricas y los workflows sí se comprueban mediante pruebas locales controladas.

Quedan preparados 10 casos de desarrollo y 20 de evaluación en `cases.jsonl`. Cuando haya cupo gratuito disponible, ejecutar:

```sh
python -m evaluation.run --split dev --output output/dev.json
python -m evaluation.run --split eval --output output/evaluation.json
```

El informe incluye modelo configurado, versión de prompt, resultados por categoría, errores, duración y tokens disponibles. La fidelidad del resumen y los hechos inventados requieren revisión humana; esos campos comienzan en `null`.

No se ha creado una issue real en GitHub. El recorrido de publicación se verifica con el nodo real de n8n apuntando a un servidor simulado local que permite reproducir éxito, rechazo, repetición y creación remota con respuesta incierta.
