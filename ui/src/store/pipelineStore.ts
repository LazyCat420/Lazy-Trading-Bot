import { create } from 'zustand';

export type PipelineEvent = {
  type: string;
  node: string;
  status: 'idle' | 'running' | 'done' | 'error';
  label?: string;
  dataIn?: string;
  dataOut?: string;
  ticker?: string;
  timestamp?: number;
  meta?: any;
};

type PipelineState = {
  events: PipelineEvent[];
  nodeStatuses: Record<string, Partial<PipelineEvent>>;
  isConnected: boolean;
  setConnected: (status: boolean) => void;
  updateNode: (event: PipelineEvent) => void;
  loadSnapshot: (snapshot: any) => void;
  clearEvents: () => void;
};

export const usePipelineStore = create<PipelineState>((set) => ({
  events: [],
  nodeStatuses: {},
  isConnected: false,
  
  setConnected: (status) => set({ isConnected: status }),
  
  updateNode: (event) => set((state) => ({
    events: [...state.events, event].slice(-100), // Keep last 100 events
    nodeStatuses: {
      ...state.nodeStatuses,
      [event.node]: {
        ...state.nodeStatuses[event.node],
        status: event.status,
        ...(event.label && { label: event.label }),
        ...(event.dataIn && { dataIn: event.dataIn }),
        ...(event.dataOut && { dataOut: typeof event.dataOut === 'string' ? event.dataOut : JSON.stringify(event.dataOut) }),
        ...(event.timestamp && { timestamp: event.timestamp }),
        ...(event.meta && { meta: event.meta }),
      }
    }
  })),
  
  loadSnapshot: (snapshot) => set((state) => {
    const statuses: Record<string, any> = {};
    if (snapshot.phases) {
      Object.keys(snapshot.phases).forEach(phase => {
        statuses[phase] = { status: snapshot.phases[phase] };
      });
    }
    return { nodeStatuses: { ...state.nodeStatuses, ...statuses } };
  }),
  
  clearEvents: () => set({ events: [] })
}));
