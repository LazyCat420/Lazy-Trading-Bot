import { useMemo, useEffect, useRef } from 'react';
import { ReactFlow, MiniMap, Controls, Background } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { usePipelineSocket } from './hooks/usePipelineSocket';
import { usePipelineStore } from './store/pipelineStore';
import { useChatStore } from './store/chatStore';
import { initialNodes } from './data/initialNodes';
import { initialEdges } from './data/initialEdges';
import PipelineNode from './components/nodes/PipelineNode';
import ChatPanel from './components/chat/ChatPanel';
import styles from './App.module.scss';
import { Activity, BotMessageSquare } from 'lucide-react';

const nodeTypes = {
  pipeline: PipelineNode,
};

function App() {
  usePipelineSocket();

  const isConnected = usePipelineStore(state => state.isConnected);
  const nodeStatuses = usePipelineStore(state => state.nodeStatuses);
  const events = usePipelineStore(state => state.events);
  const feedRef = useRef<HTMLDivElement>(null);

  const isChatOpen = useChatStore(state => state.isOpen);
  const toggleChat = useChatStore(state => state.toggleOpen);

  const nodes = useMemo(() => {
    return initialNodes.map(node => ({
        ...node,
        data: {
          ...node.data,
          ...nodeStatuses[node.id],
        }
    }));
  }, [nodeStatuses]);

  // Auto-scroll activity feed to bottom
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [events]);

  return (
    <div className={styles.appContainer}>
      <div className={styles.graphSection}>
        <ReactFlow 
          nodes={nodes} 
          edges={initialEdges} 
          nodeTypes={nodeTypes}
          fitView
          colorMode="dark"
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#333" gap={16} />
          <Controls />
          <MiniMap nodeStrokeColor="#4a5568" nodeColor="#1e1e1e" maskColor="rgba(0,0,0,0.5)" />
        </ReactFlow>

        {/* Chat toggle button */}
        <button className={styles.chatToggle} onClick={toggleChat} title="Toggle chat">
          <BotMessageSquare size={22} />
        </button>

        {/* Chat panel overlay */}
        {isChatOpen && (
          <div className={styles.chatOverlay}>
            <ChatPanel />
          </div>
        )}
      </div>
      
      <div className={styles.sidebar}>
        <div className={styles.statusBar}>
          <h2 className={styles.title}><Activity className="inline mr-2 text-blue-400" /> Pipeline Status</h2>
          <div className={styles.connectionStatus}>
            <div className={`${styles.dot} ${isConnected ? styles.connected : styles.disconnected}`}></div>
            {isConnected ? 'LIVE' : 'OFFLINE'}
          </div>
        </div>

        <div className={styles.activityFeed} ref={feedRef}>
          {events.length === 0 ? (
            <div className="text-gray-500 text-center text-sm p-4">Waiting for pipeline events...</div>
          ) : (
            events.map((ev, i) => (
              <div key={i} className={`${styles.feedItem} ${styles[ev.status]}`}>
                <span className={styles.time}>{ev.timestamp ? new Date(ev.timestamp * 1000).toLocaleTimeString() : 'Now'}</span>
                <span className={styles.phase}>{ev.node.replace('_', ' ')}:</span>
                <div className={styles.text}>{ev.label}</div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
