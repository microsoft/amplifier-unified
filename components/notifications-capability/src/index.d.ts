export interface NotificationSettings {
  revision: number;
  enabled: boolean;
  server: string;
  preview: boolean;
  topicConfigured: boolean;
  tokenConfigured: boolean;
}
export interface ServiceIdentity { installationId: string; dataScope: string; ownerId: string; instanceId: string; releaseDigest: string }
export interface QuiescenceContext { fenceId: string; commandId: string; purpose: string; instanceId: string; dataScope: string; serviceIdentity?: ServiceIdentity }
export interface NotificationReceipt {
  commandId?: string;
  session?: string;
  eventId?: string;
  kind?: 'response' | 'schedule-attention';
  status?: 'accepted' | 'dispatching' | 'server-accepted' | 'rejected' | 'unknown' | 'disabled' | 'completed';
  accepted?: boolean;
  disabled?: boolean;
  executed?: false;
  reason?: string;
  httpStatus?: number;
  deviceDelivery?: 'unverified';
  revision?: number;
  settingsRevision?: number;
  replayed?: false;
  updatedAt?: number;
}
export interface NotificationsOptions {
  owner: { command: string; args?: string[]; cwd?: string; env?: Record<string,string> };
  inspectSession(session: string): Promise<{session?: string; uri?: string; title?: string}>;
  waitForTurn(session: string, commandId: string, timeout?: number): Promise<{completed?: boolean; workContinues?: boolean; text?: string}>;
  onInvalidate?(topic: 'notifications', scope: 'host'): void | Promise<void>;
  onMayBeIdle?(): void | Promise<void>;
}
export interface TopicSnapshot { topic: 'notifications'; scope: 'host'; revision: number; data: {notificationSettings: NotificationSettings} }
export interface ActionRequest { channel: string; topic: string; version: number; operation: string; args?: Record<string,unknown>; commandId: string }
export interface NotificationsCapability {
  manifest: {version: number; topics: Record<string,{uri: string;version: number;scope: string;watch: boolean}>;actions: Record<string,{topic: string;operation: string;method: string}>};
  actionSchemas(): Promise<typeof notificationActions>;
  quiescenceAccess: Record<string,'read'>;
  quiescenceParticipant: {id: string; serviceStop: {version: 1}; acquire(context: QuiescenceContext): Promise<null|{ownerId: string;fenceId: string;release(outcome: string,proof: unknown): Promise<void>}>;reconcileRelease(input: QuiescenceContext & {outcome: string;proof?: unknown}): Promise<void>};
  inspectQuiescence(): Promise<{intakeClosed: boolean;fence: QuiescenceContext|null;calls: number;background: number}>;
  read(input: {topic: string;scope: string;uri: string}): Promise<TopicSnapshot>;
  action(input: ActionRequest, context: {session?: string|{uri?: string};clientId?: string;origin?: string}): Promise<{accepted: boolean;result: NotificationReceipt|NotificationSettings;updates: TopicSnapshot[];invalidate: string[]}>;
  turnSettled(event: {session: string;commandId: string;status: string;inputOrigin: string}): Promise<NotificationReceipt>;
  notifyAttention(input: {session: string;eventId: string;text: string}): Promise<NotificationReceipt>;
  close(): Promise<void>;
}
export const notificationActions: Record<string,{description: string;schema: Record<string,unknown>}>;
export function createNotificationsCapability(options: NotificationsOptions): NotificationsCapability;
