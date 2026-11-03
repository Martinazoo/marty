"""Fixed Spanish texts shown by the bot."""

from app.database.models import ActionStatus

PRIVATE_BOT = "Este bot es privado."

WELCOME = (
    "Hola{name}. Soy MARTY, tu asistente personal.\n\n"
    "Puedo conversar contigo, recordar lo que me cuentes y llevar tu calendario de iCloud: "
    "consultar tu agenda, buscar huecos y crear, mover o borrar eventos. El entrenador "
    "personal y el profesor de C++ llegarán en las próximas fases.\n\n"
    "Escríbeme o mándame una nota de voz con normalidad, o usa /help para ver los comandos."
)

HELP = (
    "<b>Comandos</b>\n"
    "/today - Tu agenda de hoy\n"
    "/week - Tu agenda de los próximos 7 días\n"
    "/start - Presentación\n"
    "/help - Esta ayuda\n"
    "/settings - Ver tus preferencias\n"
    "/pause [duración] - Pausar los avisos proactivos (ej.: /pause 3d, /pause 12h)\n"
    "/resume - Reactivar los avisos\n\n"
    "<b>Ejemplos</b>\n"
    "- «Trabajo de lunes a viernes de 9 a 17 en el calendario Feina»\n"
    "- «Pon CrossFit el lunes a las 20:00»\n"
    "- «¿Qué huecos libres tengo el jueves por la tarde?»\n"
    "- «Este viernes no trabajo»\n"
    "- «Recuerda que mi color favorito es el azul»\n\n"
    "También puedes mandarme notas de voz: te muestro lo que he entendido y te respondo.\n\n"
    "Antes de hacer algo importante, como borrar eventos, calendarios o recuerdos, te pediré "
    "que lo confirmes con un botón."
)

UNSUPPORTED_CONTENT = "Por ahora solo entiendo mensajes de texto y notas de voz."
VOICE_DISABLED = "Las notas de voz no están activadas. Escríbemelo, por favor."
VOICE_TOO_LONG = "El audio es demasiado largo: el máximo son {minutes} minutos."
VOICE_FAILED = "No he podido transcribir el audio ahora mismo. Prueba más tarde o escríbemelo."
VOICE_EMPTY = "No he entendido nada en el audio. ¿Me lo repites?"
UNKNOWN_COMMAND = "No conozco ese comando. Escribe /help para ver los disponibles."
ERROR = "Algo ha fallado al procesar tu mensaje. Prueba de nuevo en un momento."

CALENDAR_DISABLED = "El calendario no está conectado."
CALENDAR_UNAVAILABLE = "No puedo acceder a iCloud ahora mismo. Prueba de nuevo en un momento."

PAUSE_USAGE = (
    "Uso: /pause para pausar hasta que escribas /resume, o /pause con una duración "
    "(30m, 12h, 3d; máximo 90 días)."
)
PAUSED_UNTIL = "Avisos proactivos en pausa hasta el {until}."
PAUSED_INDEFINITELY = "Avisos proactivos en pausa hasta que escribas /resume."
RESUMED = "Avisos proactivos reactivados."
NO_PROACTIVE_YET = "Aún no envío avisos proactivos: llegarán en la fase 5."

CONFIRMATION_HEADER = "<b>Necesito tu confirmación</b>"
CONFIRM_BUTTON = "Confirmar"
CANCEL_BUTTON = "Cancelar"
ACTION_NOT_FOUND = "Esta solicitud no existe."
ALREADY_DECIDED = "Esta solicitud ya estaba decidida."
DECISION_STATUS = {
    ActionStatus.EXECUTED: "Hecho.",
    ActionStatus.REJECTED: "Cancelado. No he cambiado nada.",
    ActionStatus.EXPIRED: "Esta solicitud ha caducado. No he cambiado nada.",
    ActionStatus.FAILED: "No he podido hacerlo: ha fallado al ejecutarse.",
    ActionStatus.PENDING_CONFIRMATION: "Pendiente de confirmación.",
}

COMMANDS = (
    ("today", "Agenda de hoy"),
    ("week", "Agenda de los próximos 7 días"),
    ("start", "Presentación"),
    ("help", "Qué puede hacer MARTY"),
    ("settings", "Ver preferencias"),
    ("pause", "Pausar avisos proactivos"),
    ("resume", "Reactivar avisos proactivos"),
)
