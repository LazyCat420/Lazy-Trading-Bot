import { Handle, Position } from '@xyflow/react';
import styles from './PipelineNode.module.scss';
import type { PipelineEvent } from '../../store/pipelineStore';

const PipelineNode = ({ data }: { data: Partial<PipelineEvent> }) => {
  const statusClass = styles[data.status || 'idle'];

  return (
    <div className={`${styles.nodeWrapper} ${statusClass}`}>
      <Handle type="target" position={Position.Left} />
      
      <div className={styles.header}>
        <div className={styles.label}>{data.label || 'Unknown'}</div>
        <div className={styles.statusIndicators}>
          <div className={`${styles.primaryDot} ${statusClass}`}></div>
          {data.status === 'running' && <div className={styles.pingDot}></div>}
        </div>
      </div>
      
      {data.dataIn && <div className={styles.dataText}>IN: {data.dataIn}</div>}
      {data.dataOut && <div className={styles.dataText}>OUT: {data.dataOut}</div>}
      
      {data.timestamp && (
        <div className={styles.timestamp}>
          {new Date(data.timestamp * 1000).toLocaleTimeString()}
        </div>
      )}

      <Handle type="source" position={Position.Right} />
    </div>
  );
};

export default PipelineNode;
