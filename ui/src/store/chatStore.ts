import { create } from 'zustand';

export type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  timestamp: string;
};

type ChatState = {
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingContent: string;
  thinkingContent: string;
  error: string | null;
  isOpen: boolean;

  addMessage: (msg: ChatMessage) => void;
  appendStreamChunk: (chunk: string) => void;
  appendThinking: (chunk: string) => void;
  finalizeAssistant: () => void;
  setStreaming: (val: boolean) => void;
  setError: (err: string | null) => void;
  setOpen: (val: boolean) => void;
  toggleOpen: () => void;
  clearChat: () => void;
};

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  isStreaming: false,
  streamingContent: '',
  thinkingContent: '',
  error: null,
  isOpen: false,

  addMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg], error: null })),

  appendStreamChunk: (chunk) =>
    set((state) => ({ streamingContent: state.streamingContent + chunk })),

  appendThinking: (chunk) =>
    set((state) => ({ thinkingContent: state.thinkingContent + chunk })),

  finalizeAssistant: () => {
    const { streamingContent, thinkingContent } = get();
    if (streamingContent) {
      set((state) => ({
        messages: [
          ...state.messages,
          {
            role: 'assistant',
            content: streamingContent,
            ...(thinkingContent ? { thinking: thinkingContent } : {}),
            timestamp: new Date().toISOString(),
          },
        ],
        streamingContent: '',
        thinkingContent: '',
        isStreaming: false,
      }));
    } else {
      set({ streamingContent: '', thinkingContent: '', isStreaming: false });
    }
  },

  setStreaming: (val) => set({ isStreaming: val }),
  setError: (err) => set({ error: err, isStreaming: false }),
  setOpen: (val) => set({ isOpen: val }),
  toggleOpen: () => set((state) => ({ isOpen: !state.isOpen })),
  clearChat: () =>
    set({
      messages: [],
      streamingContent: '',
      thinkingContent: '',
      error: null,
      isStreaming: false,
    }),
}));
