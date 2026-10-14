/** Structural visibility port. Native maintenance/reset DTOs are deliberately separate. */
export interface PresentationActor {actorId:string;origin:'ui'|'agent';session?:string;}
export interface PresentationPrepare {commandId:string;operation:'reset'|'restore';selection:{kind:'explicit';sessionIds:string[]};resetCommandId?:string;}
export interface PresentationReview {
 reviewId:string;reviewHash:string;commandId:string;operation:'reset'|'restore';selectionHash:string;count:number;bytes:number;expiresAt:number;observedRevision:number;resetCommandId?:string;
 preservesCanonical:true;preservesAuthority:true;eligibility:'requires-selected-validation';
}
export interface PresentationReviewPage extends PresentationReview {items:Array<{session:string;title:string;priorHidden:boolean;targetHidden:boolean}>;nextCursor:string|null;}
export interface PresentationEffectReceipt {
 commandId:string;operation:'reset'|'restore';part:'conversation-presentation';reviewId:string;reviewHash:string;selectionHash:string;count:number;previousRevision:number;revision?:number;
 status:'completed'|'refused'|'unknown';executed?:false;preservesCanonical:true;preservesAuthority:true;reason?:string;
}
export interface PresentationReceipt {commandId:string;effect:PresentationEffectReceipt;projection:{status:'pending'|'ready'|'unknown';source:string;revision?:number};}
export interface PresentationReadiness {enabled:boolean;ready:boolean;revision:number;source?:string;projectionRevision?:number;phase:'disabled'|'checking'|'ready'|'applying'|'unknown'|'rebuilding';reason?:string;}
export interface PresentationRebuildReceipt {commandId:string;status:'completed'|'unknown';revision:number;source:string;count:number;bytes:number;markerCount:number;manifestHash:string;phase:'capturing'|'metadata'|'projection'|'ready'|'unknown';targetCheckpoint?:number;}
export interface ConversationPresentationPort {
 /** Visibility never revokes exact-ID reads, execution, receipts or owner authority. */
 presentationReset:{version:1;preservesCanonical:true;preservesAuthority:true};
 prepareConversationPresentation(input:PresentationPrepare,actor:PresentationActor):Promise<PresentationReviewPage>;
 readConversationPresentationReview(input:{reviewId:string;cursor?:string;limit?:number},actor:PresentationActor):PresentationReviewPage|Promise<PresentationReviewPage>;
 applyConversationPresentation(input:{commandId:string;reviewId:string;reviewHash:string},actor:PresentationActor):Promise<PresentationReceipt>;
 conversationPresentationReceipt(commandId:string,actor:PresentationActor):PresentationReceipt|null|Promise<PresentationReceipt|null>;
 reconcileConversationPresentation(commandId:string,actor:PresentationActor):Promise<PresentationReceipt|null>;
 inspectConversationPresentation():PresentationReadiness|Promise<PresentationReadiness>;
 rebuildConversationPresentation(input:{commandId:string;expectedRevision:number},actor:PresentationActor):Promise<PresentationRebuildReceipt>;
 reconcileConversationPresentationRebuild(commandId:string,actor:PresentationActor):Promise<PresentationRebuildReceipt|null>;
}
export interface PresentationJob {
 sessionIds:string[];actor:PresentationActor;prepareCommandId?:string;effectCommandId?:string;resetCommandId?:string;
 review?:PresentationReview;receipt?:PresentationReceipt;rebuildReceipt?:PresentationRebuildReceipt;
 /** A typed host refusal before any presentation effect was admitted. */
 preflightRefused?:true;
}
