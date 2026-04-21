import hashlib
import json

def _compute_hash_for_step(bootstrap_params: dict, step: int) -> str:
    """Başlangıç parametreleri ve anlık adımı kullanarak deterministik bir özet (hash) hesaplar."""
    # Tutarlı bir serileştirme sağla
    param_str = json.dumps(bootstrap_params, sort_keys=True)
    combined = f"{param_str}_{step}"
    
    m = hashlib.sha256()
    m.update(combined.encode('utf-8'))
    return m.hexdigest()

def get_target_port(bootstrap_params: dict, step: int) -> int:
    """Belirtilen adım için hedef portu deterministik olarak hesaplar."""
    min_p = bootstrap_params['port_range_min']
    max_p = bootstrap_params['port_range_max']
    hash_hex = _compute_hash_for_step(bootstrap_params, step)
    
    offset = int(hash_hex, 16) % (max_p - min_p + 1)
    return min_p + offset

def get_role_for_peer(bootstrap_params: dict, step: int, peer_id: int) -> str:
    """Ağ rolünü deterministik olarak hesaplar.
    'LISTENER' (Dinleyici) veya 'DIALER' (Arayıcı) döner.
    """
    hash_hex = _compute_hash_for_step(bootstrap_params, step)
    turn_indicator = int(hash_hex, 16) % 2
    
    if turn_indicator == peer_id:
        return 'LISTENER'
    else:
        return 'DIALER'
