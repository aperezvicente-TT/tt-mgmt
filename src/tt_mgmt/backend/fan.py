"""Fan backend -- board-level fan control via DeviceManager."""

try:
    from tt_mgmt.backend.smi import (
        get_devices,
        has_fan_control,
        get_fan_state,
        set_board_fan,
    )
except ImportError:  # sysfs-only install, no native backend
    get_devices = None
    has_fan_control = None
    get_fan_state = None
    set_board_fan = None


def _infer_board_type(arch: str, num_chips: int) -> str:
    if "Wormhole" in arch:
        return "N300" if num_chips >= 2 else "N150"
    if "Blackhole" in arch:
        return "P300A" if num_chips >= 2 else "P150A"
    return arch


def available() -> bool:
    """Whether fan control is usable in this install."""
    return has_fan_control is not None and has_fan_control()


def list_boards():
    """Boards exposing fan control, each with its per-ASIC state.

    Returns a list of dicts ordered by board_id, each holding the board id, an
    inferred card type, and one entry per ASIC on the board.
    """
    if get_devices is None:
        return []

    boards = {}
    for dev in get_devices():
        state = get_fan_state(dev.asic_id)
        if not state.supported:
            continue
        board = boards.setdefault(dev.board_id, {
            'board_id': dev.board_id,
            'card_type': dev.card_type or '',
            'arch': dev.arch_name,
            'asics': [],
        })
        board['asics'].append({
            'asic_id': dev.asic_id,
            'display_id': dev.display_id or '',
            'is_remote': dev.is_remote,
            'forced': state.forced,
            'target_pct': state.target_pct,
            'target_pwm': state.target_pwm,
            'fan1_rpm': state.fan1_rpm,
            'fan2_rpm': state.fan2_rpm,
        })

    result = []
    for idx, board_id in enumerate(sorted(boards)):
        board = boards[board_id]
        board['index'] = idx
        board['asics'].sort(key=lambda a: (a['is_remote'], a['asic_id']))
        if not board['card_type']:
            board['card_type'] = _infer_board_type(board['arch'], len(board['asics']))
        # The M3 drives the fan at max() of the per-ASIC targets, so the board's
        # effective duty cycle is the highest one any of its ASICs is asking for.
        board['effective_pct'] = max(a['target_pct'] for a in board['asics'])
        board['forced'] = any(a['forced'] for a in board['asics'])
        # Every ASIC on a board reads the same M3 tach, so take it from the first.
        board['rpm'] = [board['asics'][0]['fan1_rpm'], board['asics'][0]['fan2_rpm']]
        result.append(board)
    return result


def resolve_boards(board_index=None, board_id=None):
    """Select boards by list index or by raw board id. None of either = all."""
    boards = list_boards()
    if board_id is not None:
        boards = [b for b in boards if b['board_id'] == board_id]
        if not boards:
            raise ValueError(f"No fan-capable board with id 0x{board_id:x}")
    elif board_index is not None:
        boards = [b for b in boards if b['index'] == board_index]
        if not boards:
            raise ValueError(f"No fan-capable board at index {board_index}")
    return boards


def set_speed(boards, pct):
    """Apply `pct` (0-100, or None to release) to each board in `boards`."""
    if set_board_fan is None:
        raise RuntimeError("Fan control requires the native UMD backend")
    for board in boards:
        set_board_fan(board['board_id'], pct)
