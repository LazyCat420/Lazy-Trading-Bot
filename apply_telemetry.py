import glob
import os

skip_files = [
    "__init__.py",
    "unified_logger.py",
    "PipelineTracer.py",
    "pipeline_health.py",
    "ws_broadcaster.py",
    "event_logger.py",
    "artifact_logger.py",
    "decision_logger.py",
    "llm_audit_logger.py",
]

def process_file(filepath):
    with open(filepath, "r") as f:
        lines = f.readlines()
        
    new_lines = []
    changed = False
    
    for i, line in enumerate(lines):
        # Identify top-level classes
        if line.startswith("class ") and ":" in line:
            # Look up to see if it's decorated or part of a multi-line docstring
            # Check previous non-empty line
            is_decorated = False
            for prev_idx in range(i - 1, -1, -1):
                prev_line = lines[prev_idx].strip()
                if not prev_line:
                    continue
                if "@track_class_telemetry" in prev_line:
                    is_decorated = True
                break
            
            if not is_decorated:
                new_lines.append("@track_class_telemetry\n")
                changed = True

        new_lines.append(line)
        
    if changed:
        has_import = any("unified_logger" in l for l in new_lines)
        if not has_import:
            # Find the right place to insert import (after __future__ or module docstring)
            insert_idx = 0
            for idx, ln in enumerate(new_lines):
                clean_ln = ln.strip()
                if clean_ln.startswith("import ") or clean_ln.startswith("from "):
                    if "__future__" not in clean_ln:
                        insert_idx = idx
                        break
            
            import_stmt = "from app.services.unified_logger import track_class_telemetry, track_telemetry\n"
            new_lines.insert(insert_idx, import_stmt)
            
        with open(filepath, "w") as f:
            f.writelines(new_lines)
        print(f"Injected telemetry into {os.path.basename(filepath)}")


def main():
    service_files = glob.glob("app/services/*.py")
    for filepath in service_files:
        filename = os.path.basename(filepath)
        if filename in skip_files:
            continue
        process_file(filepath)


if __name__ == "__main__":
    main()
