# HF Propagation Data — España e IN91PO

Recopilación, validación y publicación automática de datos de propagación HF para España y para la cuadrícula Maidenhead **IN91PO**.

El objetivo del proyecto es generar datos estructurados y verificables que puedan utilizarse en informes periódicos de propagación HF, evitando depender únicamente de mapas visuales o predicciones genéricas.

## Objetivos

- Obtener valores actuales de **foF2** y **MUF(3000)**.
- Estimar las condiciones NVIS en **80, 40 y 20 metros**.
- Analizar la evolución de la ionosfera durante la última hora.
- Diferenciar condiciones para España, Europa, Norteamérica, Sudamérica, África, Asia y Oceanía.
- Incorporar actividad de radio observada, no solo predicciones.
- Publicar los resultados en archivos JSON accesibles públicamente.
- Asignar un nivel de fiabilidad según la disponibilidad y actualidad de cada fuente.

## Ubicación de referencia

La evaluación local se realiza para:

- **Locator:** IN91PO
- **Latitud aproximada:** 41.6042
- **Longitud aproximada:** -0.7083

También se utilizan diferentes puntos representativos de España peninsular, Baleares y Canarias.

## Fuentes previstas

### KC2G Propagation

KC2G utiliza datos de ionosondas procedentes de GIRO y genera mapas ionosféricos globales.

Fuentes utilizadas:

- `https://prop.kc2g.com/api/stations.json`
- `https://prop.kc2g.com/api/latest_run.json`
- `https://prop.kc2g.com/api/available_maps.json`
- `https://prop.kc2g.com/api/assimilated.h5`
- `https://prop.kc2g.com/renders/current/mufd-normal-now.svg`
- `https://prop.kc2g.com/renders/current/fof2-normal-now.svg`

Datos principales:

- **foF2:** frecuencia crítica de la capa F2, especialmente útil para estimar NVIS.
- **MUF(3000):** máxima frecuencia utilizable estimada para un salto de unos 3000 km.
- Hora de la medida.
- Antigüedad de los datos.
- Confidence score de las ionosondas.
- Evolución durante los cuatro intervalos anteriores de 15 minutos.

### DXView

Perspectiva utilizada:

- `https://hf.dxview.org/perspective/IN91PO`

DXView representa actividad observada en bandas HF a partir de fuentes como:

- WSPRnet.
- Reverse Beacon Network.
- PSKReporter.
- DX Cluster.
- DXWatch.

La integración directa de la capa dinámica de DXView se encuentra en investigación. No se considerará integrada hasta poder obtener datos actuales y reproducibles de banda, hora, ruta, intensidad o SNR.

Cuando no exista acceso directo, se podrá generar una reconstrucción utilizando las fuentes originales, agrupando la actividad por banda, modo, rumbo desde IN91PO, distancia, ventanas de 15 minutos, número de estaciones independientes y persistencia temporal.

### NOAA / SWPC

Se utilizarán datos actuales de meteorología espacial:

- SFI.
- Kp.
- Índice A.
- Viento solar.
- Bz.
- Rayos X.
- Protones.
- Escalas R, S y G.
- Absorción de la capa D.

### HamQSL / N0NBH

Panel global de condiciones de propagación:

- `https://www.hamqsl.com/solarn0nbh.php`

Sus indicadores globales se utilizarán como referencia complementaria, pero no sustituirán la evaluación específica para España o IN91PO.

## Reglas de validación

Una fuente no se considera operativa únicamente porque su URL responda.

Cada ejecución debe diferenciar estas etapas:

1. Endpoint localizado.
2. Respuesta recibida.
3. Formato interpretado correctamente.
4. Actualidad de los datos comprobada.
5. Valor local, regional o interpolado obtenido.

Solo se asignará peso a una fuente cuando se hayan completado las etapas necesarias.

Ejemplo:

```json
{
  "endpoint_located": true,
  "response_received": true,
  "format_parsed": true,
  "current_data_checked": true,
  "local_value_obtained": true
}
```

Si una fuente no responde, está obsoleta o no puede interpretarse, tendrá peso cero en esa ejecución.

## Control de antigüedad

| Antigüedad | Estado |
|---|---|
| Hasta 45 minutos | Fresh |
| 45–90 minutos | Degraded |
| 90–180 minutos | Stale |
| Más de 180 minutos | Unusable |

Los datos obsoletos no deben utilizarse como si describieran el estado actual de la ionosfera.

## Archivos publicados

El proyecto publicará archivos similares a los siguientes:

```text
data/kc2g-in91po.json
data/kc2g-spain.json
data/dxview-in91po.json
data/space-weather.json
data/hf-report.json
```

Ejemplo de dirección pública mediante GitHub Pages:

```text
https://USUARIO.github.io/hf-propagation-data/data/kc2g-in91po.json
```

Sustituir `USUARIO` por el nombre de usuario real de GitHub.

## Ejemplo de resultado KC2G

```json
{
  "source": "KC2G assimilated HDF5",
  "locator": "IN91PO",
  "coordinates": {
    "latitude": 41.6042,
    "longitude": -0.7083
  },
  "timestamp": "2026-07-16T19:15:00Z",
  "age_minutes": 12,
  "fof2_mhz": 6.4,
  "mufd_mhz": 18.7,
  "trend": {
    "fof2": "falling",
    "mufd": "stable"
  },
  "validation": {
    "endpoint_located": true,
    "response_received": true,
    "format_parsed": true,
    "current_data_checked": true,
    "local_value_obtained": true
  }
}
```

Los valores anteriores son únicamente un ejemplo del formato y no representan una medida real.

## Interpretación NVIS

La evaluación NVIS tendrá en cuenta principalmente:

- foF2.
- Absorción de la capa D.
- Hora local.
- Ángulo de radiación.
- Distancia del enlace.
- Tendencia de la última hora.

Referencias prácticas:

- **80 m:** puede perder cobertura cercana cuando la foF2 baja aproximadamente de 3 MHz.
- **40 m:** necesita que la foF2 se acerque a 7 MHz para ofrecer cobertura realmente corta.
- **20 m:** normalmente no es adecuada para NVIS nacional.

Estos valores son orientativos y deben interpretarse junto con el resto de las condiciones.

## Automatización

Los procesos se ejecutarán mediante GitHub Actions:

- KC2G: cada 15 minutos.
- NOAA y meteorología espacial: cada hora o según disponibilidad.
- DXView y actividad observada: cada hora mientras se valida el método.
- Publicación automática mediante GitHub Pages.

La hora de ejecución puede desplazarse unos minutos para evitar la saturación habitual al inicio de cada cuarto de hora.

## Estado del proyecto

- [ ] Lectura actual de KC2G validada de extremo a extremo.
- [ ] Interpolación para IN91PO validada con datos reales.
- [ ] Histórico de cuatro intervalos de 15 minutos validado.
- [ ] Resumen regional de España validado.
- [ ] Datos NOAA integrados.
- [ ] Panel HamQSL interpretado automáticamente.
- [ ] Endpoint interno de DXView identificado.
- [ ] Reconstrucción alternativa de DXView validada.
- [ ] Publicación mediante GitHub Pages.
- [ ] Integración con los avisos horarios HF.

## Limitaciones

- Los datos ionosféricos de IN91PO son interpolaciones y no una medida directa de una ionosonda situada en el locator.
- Una MUF local elevada no garantiza por sí sola una ruta DX completa; importa la peor zona de todo el trayecto.
- La actividad observada depende de que existan operadores y estaciones automáticas transmitiendo.
- Una banda sin spots no tiene por qué estar completamente cerrada.
- Las predicciones pueden variar rápidamente durante fulguraciones, tormentas geomagnéticas o cambios del viento solar.
- El proyecto no debe presentar valores estimados como mediciones reales.

## Uso responsable

Este proyecto debe respetar:

- Las condiciones de uso de las fuentes.
- Los límites de consulta de cada servicio.
- La atribución correspondiente.
- La prohibición de utilizar endpoints privados o eludir controles de acceso.

## Licencia

Pendiente de seleccionar.

Una opción adecuada para el código sería la licencia MIT. Los datos obtenidos de terceros mantienen las condiciones y derechos establecidos por sus fuentes originales.

## Autor

Proyecto orientado a la generación de avisos de propagación HF para EA y DX, con especial atención a España y a la ubicación IN91PO.
