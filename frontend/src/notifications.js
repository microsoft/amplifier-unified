export function notificationBody(message,settings={}){
 return settings.preview===true?(message.text?.slice(0,180)||'Your response is ready.'):'Your Amplifier response is ready.';
}
export function desktopNotificationsEnabled(settings={}){return settings.desktop!==false}
