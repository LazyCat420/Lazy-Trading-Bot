import { useEffect, useRef } from 'react';
import type { PipelineEvent } from '../store/pipelineStore';
import { usePipelineStore } from '../store/pipelineStore';

export const usePipelineSocket = () => {
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelay = useRef(1000);
  
  const setConnected = usePipelineStore((state) => state.setConnected);
  const updateNode = usePipelineStore((state) => state.updateNode);
  const loadSnapshot = usePipelineStore((state) => state.loadSnapshot);

  const connect = () => {
    // Protocol relative websocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // If dev, hitting proxy, else relative
    const host = import.meta.env.DEV ? 'localhost:4000' : window.location.host;
    
    ws.current = new WebSocket(`${protocol}//${host}/ws/pipeline`);
    
    ws.current.onopen = () => {
      console.log('WS Connected');
      setConnected(true);
      reconnectDelay.current = 1000; // Reset delay
    };
    
    ws.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'snapshot') {
          loadSnapshot(data.data);
        } else if (data.type === 'phase_update') {
          updateNode(data as PipelineEvent);
        }
      } catch (err) {
        console.error('Failed to parse WS message', err);
      }
    };
    
    ws.current.onclose = () => {
      console.log('WS Disconnected');
      setConnected(false);
      
      // Auto-reconnect with exponential backoff max 5s
      reconnectTimeout.current = setTimeout(() => {
        reconnectDelay.current = Math.min(reconnectDelay.current * 2, 5000);
        connect();
      }, reconnectDelay.current);
    };
    
    ws.current.onerror = (err) => {
      console.error('WS Error:', err);
      // Let onclose handle reconnects
    };
  };

  // Initial fetch for snapshot immediately before WS connects
  useEffect(() => {
    fetch('/api/pipeline/snapshot')
      .then(res => res.json())
      .then(data => {
        loadSnapshot(data);
      })
      .catch(err => console.error('Failed to fetch snapshot', err));
  }, []);

  useEffect(() => {
    connect();
    
    return () => {
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current);
      if (ws.current) {
        ws.current.onclose = null; // Don't reconnect on intentional unmount cleanup
        ws.current.close();
      }
    };
  }, []);
};
