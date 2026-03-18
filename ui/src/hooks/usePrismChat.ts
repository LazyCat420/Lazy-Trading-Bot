import { useRef, useCallback } from 'react';
import { useChatStore } from '../store/chatStore';
import PrismService from '../services/PrismService';

const DEFAULT_PROVIDER = 'google';
const DEFAULT_MODEL = 'gemini-2.5-flash';

export const usePrismChat = () => {
  const abortRef = useRef<(() => void) | null>(null);

  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const error = useChatStore((s) => s.error);
  const addMessage = useChatStore((s) => s.addMessage);
  const appendStreamChunk = useChatStore((s) => s.appendStreamChunk);
  const appendThinking = useChatStore((s) => s.appendThinking);
  const finalizeAssistant = useChatStore((s) => s.finalizeAssistant);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setError = useChatStore((s) => s.setError);

  const sendMessage = useCallback(
    (text: string) => {
      if (!text.trim() || isStreaming) return;

      // Add user message
      const userMsg = {
        role: 'user' as const,
        content: text.trim(),
        timestamp: new Date().toISOString(),
      };
      addMessage(userMsg);
      setStreaming(true);

      // Build messages array for Prism (full conversation history)
      const allMessages = [
        ...messages.map((m) => ({ role: m.role, content: m.content })),
        { role: 'user', content: text.trim() },
      ];

      // Abort any previous stream
      if (abortRef.current) abortRef.current();

      const payload = {
        provider: DEFAULT_PROVIDER,
        model: DEFAULT_MODEL,
        messages: allMessages,
      };

      // Use PrismService.streamText — same pattern as Retina
      abortRef.current = PrismService.streamText(payload, {
        onChunk: (content) => {
          appendStreamChunk(content);
        },
        onThinking: (content) => {
          appendThinking(content);
        },
        onDone: () => {
          finalizeAssistant();
        },
        onError: (err) => {
          setError(err.message || 'Unknown error from Prism');
        },
      });
    },
    [
      messages,
      isStreaming,
      addMessage,
      appendStreamChunk,
      appendThinking,
      finalizeAssistant,
      setStreaming,
      setError,
    ],
  );

  const cancelStream = useCallback(() => {
    if (abortRef.current) {
      abortRef.current();
      abortRef.current = null;
    }
    finalizeAssistant();
  }, [finalizeAssistant]);

  return { sendMessage, cancelStream, isStreaming, error };
};
