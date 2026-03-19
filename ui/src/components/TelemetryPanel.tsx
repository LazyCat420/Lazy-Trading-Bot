import { useState, useEffect } from 'react';
import { Activity, Clock, Database, AlertCircle, CheckCircle2 } from 'lucide-react';

interface TelemetryStep {
  step_name: string;
  status: 'success' | 'failed';
  duration_ms: number;
  input_size: number;
  output_size: number;
  fail_reason: string;
  timestamp: string;
}

interface TelemetryResponse {
  cycle_id: string | null;
  steps: TelemetryStep[];
}

export default function TelemetryPanel() {
  const [data, setData] = useState<TelemetryResponse>({ cycle_id: null, steps: [] });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    
    const fetchTelemetry = async () => {
      try {
        const res = await fetch('/api/pipeline/telemetry');
        if (!res.ok) throw new Error('API returned ' + res.status);
        const json = await res.json();
        if (mounted && json.steps) {
          setData(json);
          setError(null);
        }
      } catch (err) {
        if (mounted) {
          console.error('[Telemetry] Fetch error:', err);
          setError('Live Telemetry Offline');
        }
      }
    };

    // Poll every 2 seconds
    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 2500);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="bg-[#1e1e1e] border-l border-gray-800 flex flex-col h-full overflow-hidden" style={{ minWidth: '340px' }}>
      <div className="p-4 border-b border-gray-800 bg-[#252525] flex justify-between items-center shrink-0">
        <h2 className="text-white font-bold flex items-center gap-2">
          <Activity size={18} className="text-purple-400" />
          Live Microservices
        </h2>
        {error ? (
          <span className="text-xs font-semibold px-2 py-1 bg-red-900/40 text-red-400 rounded-md flex items-center gap-1">
            <AlertCircle size={12} /> Offline
          </span>
        ) : (
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
            <span className="text-xs text-gray-400 font-mono">
              Cycle: {data.cycle_id ? data.cycle_id.substring(0, 8) : 'IDLE'}
            </span>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {data.steps.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-gray-500 space-y-3">
            <Database size={32} className="opacity-50" />
            <p className="text-sm">Initiate an Autonomous Loop to view the microservice trace map.</p>
          </div>
        ) : (
          data.steps.map((step, i) => (
            <div 
              key={i} 
              className={`p-3 rounded-lg border ${
                step.status === 'success' 
                  ? 'bg-green-900/10 border-green-900/30' 
                  : 'bg-red-900/20 border-red-900/50'
              }`}
            >
              <div className="flex justify-between items-start mb-1">
                <span className="text-sm font-mono font-medium text-gray-200">
                  {step.step_name.replace('Service.', '.')}
                </span>
                {step.status === 'success' ? (
                  <CheckCircle2 size={16} className="text-green-500" />
                ) : (
                  <AlertCircle size={16} className="text-red-500" />
                )}
              </div>
              
              <div className="flex gap-4 text-xs text-gray-500 mt-2">
                <span className="flex items-center gap-1">
                  <Clock size={12} />
                  {step.duration_ms.toFixed(1)}ms
                </span>
                <span className="flex items-center gap-1" title="I/O Payload Size (bytes)">
                  <Database size={12} />
                  {(step.input_size + step.output_size) > 1024 
                    ? ((step.input_size + step.output_size)/1024).toFixed(1) + 'kb' 
                    : (step.input_size + step.output_size) + 'b'}
                </span>
              </div>

              {step.status === 'failed' && step.fail_reason && (
                <div className="mt-2 p-2 bg-red-950/30 rounded text-xs text-red-300 font-mono break-words">
                  {step.fail_reason}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
