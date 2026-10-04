/** Trusted composition and wire shapes; the upstream AHP envelope is unchanged. */
export type TerminalPlatform = 'linux-arm64' | 'linux-x86_64' | 'macos-arm64' | 'macos-x86_64';
export interface TerminalPreparation {
 version: 1; preparationId: string; artifactId: string;
 artifact: {manifestSha256: string; wheelSha256: string; version: string; sourceCommit: string};
 platform: TerminalPlatform; name: string; origin: string;
 /** Unix seconds, original creation time + 1800. Never extended on retry. */
 expiresAt: number; download: {url: string; filename: string};
}
export interface TerminalDevice {
 deviceId: string; preparationId: string; artifactId: string;
 platform: TerminalPlatform; name: string; createdAt: number; revokedAt: number | null;
}
export interface TerminalReceipt {
 commandId: string; operation: 'terminal.prepare' | 'terminal.revoke';
 status: 'completed' | 'failed' | 'unknown'; replayed: false;
 /** Only definitive refusals before any effect carry this field. */
 executed?: false;
 result?: TerminalPreparation | {deviceId: string; revokedAt: number; admittedWork: 'continues'};
 error?: {code: string};
}
export interface TerminalActions {
 'terminal.prepare': {platform: TerminalPlatform; name: string; artifactId?: string};
 'terminal.devices': {cursor?: string; limit?: number}; // 1..50
 'terminal.revoke': {deviceId: string};
 'terminal.receipt': {commandId: string}; // original, <=256 characters
}
export interface TerminalRedemption {
 preparationId: string; grant: string; redemptionId: string; artifactId: string;
}
/** Returned once, only in the authenticated private HTTP redemption response. */
export interface TerminalRegistered {
 version: 1; status: 'registered'; preparationId: string; redemptionId: string;
 artifactId: string; origin: string; deviceId: string; token: string; name: string; createdAt: number;
}
export interface TerminalConsumed {
 version: 1; status: 'consumed'; preparationId: string; artifactId: string; origin: string;
 credentialAvailable: false;
 /** Included only when the original redemptionId matches. No token recovery. */
 redemptionId?: string; deviceId?: string; createdAt?: number;
}
/** This port is supplied by the actual configured owner, never wire arguments. */
export interface TerminalAccess {
 origin: string;
 /** Synchronously verifies and attaches to eliminate revoke/handshake races. */
 attachDevice(authorization: string, stop: () => void): {account: string; deviceId: string; release(): void};
 handleRedemption(request: unknown, response: unknown): Promise<void>;
}
