export {};

declare global {
  interface SylphyVoicePlaybackStatus {
    enabled: boolean;
    state: string;
    is_speaking: boolean;
    active_session_id: string | null;
    entity_instance_id: string | null;
    session_generation: number;
    sequence: number;
    position_seconds: number;
    duration_seconds: number;
    amplitude: number;
  }

  interface Window {
    sylphyDesktop?: {
      minimize(): Promise<void>;
      hide(): Promise<void>;
      toggleAlwaysOnTop(): Promise<boolean>;
      getWindowState(): Promise<{
        alwaysOnTop: boolean;
        bounds: { x: number; y: number; width: number; height: number } | null;
      }>;
      getEntityStatus(): Promise<{ connected: boolean; lifecycle: string; error?: string }>;
      getVoiceStatus(): Promise<SylphyVoicePlaybackStatus>;
      getAvatarPresentation(): Promise<{
        revision: number;
        behavior: 'idle' | 'processing' | 'reacting' | 'speaking' | 'interrupted' | 'paused' | 'shutdown';
        commands: Array<{
          action: 'start' | 'stop'; command_id: string; owner: string; behavior: string;
          clip: string; region: 'body' | 'head' | 'eyes' | 'face' | 'mouth'; priority: number;
          loop: boolean; fade_in_seconds: number; fade_out_seconds: number; weight: number;
          session_id: string | null; sequence: number;
        }>;
      } | null>;
      beginWindowMove(screenX: number, screenY: number): void;
      moveWindow(screenX: number, screenY: number): void;
      endWindowMove(): void;
    };
  }
}
