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

def _compute_hash_for_step_and_peer(bootstrap_params: dict, step: int, peer_id: int) -> str:
    """Belirli bir peer için deterministik port üretim özeti hesaplar."""
    param_str = json.dumps(bootstrap_params, sort_keys=True)
    combined = f"{param_str}_{step}_peer_{peer_id}"

    m = hashlib.sha256()
    m.update(combined.encode('utf-8'))
    return m.hexdigest()

def get_port_for_peer(bootstrap_params: dict, step: int, peer_id: int) -> int:
    """Belirtilen adım ve peer için yerel portu deterministik olarak hesaplar."""
    min_p = bootstrap_params['port_range_min']
    max_p = bootstrap_params['port_range_max']
    hash_hex = _compute_hash_for_step_and_peer(bootstrap_params, step, peer_id)

    offset = int(hash_hex, 16) % (max_p - min_p + 1)
    return min_p + offset

def _get_distinct_ports(bootstrap_params: dict, step: int) -> tuple[int, int]:
    """İki peer için çakışmasız port çifti üretir."""
    min_p = bootstrap_params['port_range_min']
    max_p = bootstrap_params['port_range_max']
    port_0 = get_port_for_peer(bootstrap_params, step, 0)
    port_1 = get_port_for_peer(bootstrap_params, step, 1)

    if port_0 == port_1 and max_p > min_p:
        port_1 = min_p + ((port_1 - min_p + 1) % (max_p - min_p + 1))

    return port_0, port_1

def get_port_pair(bootstrap_params: dict, step: int, peer_id: int) -> tuple[int, int]:
    """Yerel peer portu ve karşı peer portunu döner."""
    port_0, port_1 = _get_distinct_ports(bootstrap_params, step)
    local_port = port_0 if peer_id == 0 else port_1
    peer_port = port_1 if peer_id == 0 else port_0
    return local_port, peer_port

def get_target_port(bootstrap_params: dict, step: int) -> int:
    """Geriye dönük uyumluluk için listener peer portunu döner."""
    hash_hex = _compute_hash_for_step(bootstrap_params, step)
    listener_peer_id = int(hash_hex, 16) % 2
    port_0, port_1 = _get_distinct_ports(bootstrap_params, step)
    return port_0 if listener_peer_id == 0 else port_1

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

def get_emergency_port_pair(bootstrap_params: dict, step: int, peer_id: int) -> tuple[int, int]:
    """Emergency hop için yedek port çifti hesaplar.
    Normal portlardan bağımsız, 'emergency' salt'ı ile deterministik üretilir."""
    min_p = bootstrap_params['port_range_min']
    max_p = bootstrap_params['port_range_max']
    param_str = json.dumps(bootstrap_params, sort_keys=True)

    hash_0 = hashlib.sha256(f"{param_str}_{step}_peer_0_emergency".encode('utf-8')).hexdigest()
    hash_1 = hashlib.sha256(f"{param_str}_{step}_peer_1_emergency".encode('utf-8')).hexdigest()

    port_0 = min_p + (int(hash_0, 16) % (max_p - min_p + 1))
    port_1 = min_p + (int(hash_1, 16) % (max_p - min_p + 1))

    if port_0 == port_1 and max_p > min_p:
        port_1 = min_p + ((port_1 - min_p + 1) % (max_p - min_p + 1))

    local_port = port_0 if peer_id == 0 else port_1
    peer_port = port_1 if peer_id == 0 else port_0
    return local_port, peer_port
