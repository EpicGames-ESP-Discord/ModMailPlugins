# Report (Modmail plugin)

Menús contextuales (Apps):
- **Reportar usuario**: modal con razón + adjuntos (obligatorios).
- **Reportar mensaje**: modal con razón + adjuntos (opcionales); incluye contenido, autor y enlace del mensaje.

Al enviar el modal se crea un thread de Modmail a nombre del reportante (o se reutiliza el existente) con un embed del reporte y los adjuntos.

## Instalación
- Local: copiar la carpeta a `plugins/@local/report` y ejecutar `?plugin load @local/report`.
- GitHub: `?plugin add usuario/repo/report`.

Requiere discord.py >= 2.6 para `FileUpload` en modales. En versiones anteriores se usa un campo de texto para enlaces de evidencia.
