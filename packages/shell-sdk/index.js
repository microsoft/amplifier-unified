/** Public, dependency-free entry point. Bundle this helper into a native module. */
export function defineModule(factory){
 if(typeof factory!=='function')throw new TypeError('A shell module exports a component factory.');
 return factory;
}
export function useNavigation(React,host){
 return React.useSyncExternalStore(host.subscribe,host.getSnapshot,host.getSnapshot);
}
export function useCanvas(React,host){
 return React.useSyncExternalStore(host.subscribe,host.getSnapshot,host.getSnapshot);
}
