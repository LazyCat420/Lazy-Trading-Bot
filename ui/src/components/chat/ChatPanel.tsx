import { useState, useRef, useEffect } from 'react';
import { usePrismChat } from '../../hooks/usePrismChat';
import { useChatStore } from '../../store/chatStore';
import { Send, Square, Trash2, X, ChevronDown, ChevronRight, BotMessageSquare } from 'lucide-react';
import styles from './ChatPanel.module.scss';

const ChatPanel = () => {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [expandedThinking, setExpandedThinking] = useState<Set<number>>(new Set());

  const messages = useChatStore((s) => s.messages);
  const streamingContent = useChatStore((s) => s.streamingContent);
  const thinkingContent = useChatStore((s) => s.thinkingContent);
  const clearChat = useChatStore((s) => s.clearChat);
  const toggleOpen = useChatStore((s) => s.toggleOpen);

  const { sendMessage, cancelStream, isStreaming, error } = usePrismChat();

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent, thinkingContent]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSend = () => {
    if (!input.trim() || isStreaming) return;
    sendMessage(input);
    setInput('');
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const toggleThinking = (index: number) => {
    setExpandedThinking((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  return (
    <div className={styles.panel}>
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.headerTitle}>
          <BotMessageSquare size={18} />
          <span>Prism Chat</span>
        </div>
        <div className={styles.headerActions}>
          <button
            className={styles.iconBtn}
            onClick={clearChat}
            title="Clear chat"
          >
            <Trash2 size={15} />
          </button>
          <button
            className={styles.iconBtn}
            onClick={toggleOpen}
            title="Close"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className={styles.messageList}>
        {messages.length === 0 && !isStreaming && (
          <div className={styles.emptyState}>
            <BotMessageSquare size={36} strokeWidth={1.2} />
            <p>Ask about your portfolio, market data, or trading strategies.</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`${styles.message} ${styles[msg.role]}`}
          >
            {msg.thinking && (
              <button
                className={styles.thinkingToggle}
                onClick={() => toggleThinking(i)}
              >
                {expandedThinking.has(i) ? (
                  <ChevronDown size={13} />
                ) : (
                  <ChevronRight size={13} />
                )}
                <span>Thinking</span>
              </button>
            )}
            {msg.thinking && expandedThinking.has(i) && (
              <div className={styles.thinkingBlock}>{msg.thinking}</div>
            )}
            <div className={styles.messageContent}>{msg.content}</div>
          </div>
        ))}

        {/* Streaming in progress */}
        {isStreaming && (
          <div className={`${styles.message} ${styles.assistant}`}>
            {thinkingContent && (
              <div className={styles.thinkingBlock}>
                {thinkingContent}
                <span className={styles.cursor} />
              </div>
            )}
            {streamingContent ? (
              <div className={styles.messageContent}>
                {streamingContent}
                <span className={styles.cursor} />
              </div>
            ) : !thinkingContent ? (
              <div className={styles.dots}>
                <span /><span /><span />
              </div>
            ) : null}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className={styles.errorBanner}>{error}</div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className={styles.inputArea}>
        <textarea
          ref={inputRef}
          className={styles.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Send a message…"
          rows={1}
          disabled={isStreaming}
        />
        {isStreaming ? (
          <button className={styles.sendBtn} onClick={cancelStream} title="Stop">
            <Square size={16} />
          </button>
        ) : (
          <button
            className={styles.sendBtn}
            onClick={handleSend}
            disabled={!input.trim()}
            title="Send"
          >
            <Send size={16} />
          </button>
        )}
      </div>
    </div>
  );
};

export default ChatPanel;
