import React,{createContext,useContext,useState} from 'react';
import {createPortal} from 'react-dom';

// Controls share one stable surface. Pinning changes its layout, never the viewers.
export const CanvasControlsHost=createContext(undefined);
// Primary common actions have their own stable compact row. Inline viewers keep
// their existing controls: absence of this context is not a second toolbar.
export const CanvasPrimaryActionsHost=createContext(undefined);
export const usePrimaryCanvas=()=>useContext(CanvasPrimaryActionsHost)!==undefined;
export function CanvasPrimaryAction({children}){
 const host=useContext(CanvasPrimaryActionsHost);
 return host===undefined?<CanvasControl>{children}</CanvasControl>:host?createPortal(children,host):null;
}
export function CanvasControl({children,inline=false}){
 const host=useContext(CanvasControlsHost);
 return inline||host===undefined?children:host?createPortal(children,host):null;
}
export function CanvasViewControls({children,label}){
 const host=useContext(CanvasControlsHost),[target,setTarget]=useState(null);
 if(host===undefined)return children;
 return <><CanvasControl><div ref={setTarget} className="a-canvas-view-tools" role="group" aria-label={label}/></CanvasControl><CanvasControlsHost.Provider value={target}>{children}</CanvasControlsHost.Provider></>;
}
