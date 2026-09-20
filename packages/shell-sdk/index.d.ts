export interface ShellHost {
 readonly apiVersion: '1.0';
 readonly clientId: string;
 readonly instanceId: string;
 getSnapshot(): Readonly<NavigationSnapshot>;
 subscribe(listener: () => void): () => void;
 dispatch(action: string, args?: Record<string, unknown>): Promise<{accepted: boolean; result?: unknown}>;
 setDirty(dirty: boolean): Promise<unknown>;
}
export interface NavigationSnapshot {
 readonly view: Readonly<Record<string, unknown>>;
 readonly selectedWorkspaceId: string | null;
 readonly selectedSessionId: string | null;
 readonly workspaces: readonly {id:string; name:string; path:string; available:boolean}[];
 readonly chatNavigation: {items:readonly {id:string;title:string;workspace:string;workspaceId:string;pinned:boolean;status:string}[];total:number;index:number;pages:number;scope:Record<string,unknown>};
 readonly workspaceExplorer: {rows:readonly {path:string;name:string;workspaceId?:string;chatCount?:number}[];page:number;pages:number;filter:string};
}
/** React is supplied by the host; native packages must not bundle another copy. */
export function defineModule<T>(factory: (runtime: {React: unknown}) => T): typeof factory;
export function useNavigation(React: any, host: ShellHost): NavigationSnapshot;
export interface CanvasSnapshot {
 readonly viewId: string;
 readonly resource: null | {id:string;kind:string;title?:string;content?:string;surface?:unknown;url?:string;path?:string;revision:string};
 readonly view: Readonly<Record<string, unknown>>;
}
export interface CanvasHost {
 readonly apiVersion: '1.0';
 readonly instanceId: string;
 readonly viewId: string;
 getSnapshot(): Readonly<CanvasSnapshot>;
 /** Explicit source read, including large stored documents. Rejects stale view bindings. */
 readSource(): Promise<string>;
 subscribe(listener: () => void): () => void;
 dispatch(action: 'view.update' | 'view.report', args: Record<string, unknown>): Promise<unknown>;
 setDirty(dirty: boolean): Promise<unknown>;
}
export function useCanvas(React: any, host: CanvasHost): CanvasSnapshot;
