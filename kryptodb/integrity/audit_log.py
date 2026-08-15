import sqlite3
import hashlib
import json
import os
from pathlib import Path

class AuditLogger:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
        
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    prev_hash TEXT NOT NULL,
                    entry_json TEXT NOT NULL,
                    entry_hash TEXT NOT NULL
                )
            ''')
            
            cursor = conn.execute('SELECT COUNT(*) FROM audit_log')
            if cursor.fetchone()[0] == 0:
                genesis_hash = hashlib.sha256(b"GENESIS").hexdigest()
                conn.execute('INSERT INTO audit_log (prev_hash, entry_json, entry_hash) VALUES (?, ?, ?)',
                             ("0000000000000000000000000000000000000000000000000000000000000000", "{}", genesis_hash))

    def get_last_hash(self) -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1')
            row = cursor.fetchone()
            return row[0] if row else "0000000000000000000000000000000000000000000000000000000000000000"

    def log_mutation(self, action: str, agent_id: str, edge_id: str, permission_scope: str):
        entry = {
            "action": action,
            "agent_id": agent_id,
            "edge_id": edge_id,
            "permission_scope": permission_scope
        }
        entry_json = json.dumps(entry, sort_keys=True)
        prev_hash = self.get_last_hash()
        
        combined = f"{prev_hash}{entry_json}".encode()
        entry_hash = hashlib.sha256(combined).hexdigest()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT INTO audit_log (prev_hash, entry_json, entry_hash) VALUES (?, ?, ?)',
                         (prev_hash, entry_json, entry_hash))

    def log_write(self, actor: str, action: str, target_id: str, scope: str, result: str, details: str = ""):
        self.log_mutation(
            action=action,
            agent_id=actor,
            edge_id=target_id,
            permission_scope=scope,
        )
    def verify_chain(self) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT seq, prev_hash, entry_json, entry_hash FROM audit_log ORDER BY seq ASC')
            rows = cursor.fetchall()
            
            if not rows:
                return True
                
            last_hash = "0000000000000000000000000000000000000000000000000000000000000000"
            for row in rows:
                seq, prev_hash, entry_json, entry_hash = row
                if prev_hash != last_hash:
                    return False
                    
                if seq == 1 and entry_json == "{}":
                    expected_hash = hashlib.sha256(b"GENESIS").hexdigest()
                else:
                    combined = f"{prev_hash}{entry_json}".encode()
                    expected_hash = hashlib.sha256(combined).hexdigest()
                    
                if entry_hash != expected_hash:
                    return False
                    
                last_hash = entry_hash
                
        return True


audit_logger = AuditLogger(Path(os.environ.get("KRYPTODB_AUDIT_DB", "data/audit.db")))
