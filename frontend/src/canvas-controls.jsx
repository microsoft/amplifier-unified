import React,{createContext,useContext,useState} from 'react';
import {createPortal} from 'react-dom';

// Controls share one stable surface. Pinning changes its layout, never the viewers.
export const CanvasControlsHost=createContext(undefined);
export function CanvasControl({children,inline=false}){
 const host=useContext(CanvasControlsHost);
 return inline||host===undefined?children:host?createPortal(children,host):null;
}
export function CanvasViewControls({children,label}){
 const host=useContext(CanvasControlsHost),[target,setTarget]=useState(null);
 if(host===undefined)return children;
 return <><CanvasControl><div ref={setTarget} className="a-canvas-view-tools" role="group" aria-label={label}/></CanvasControl><CanvasControlsHost.Provider value={target}>{children}</CanvasControlsHost.Provider></>;
}
