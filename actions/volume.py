# System volume via pycaw, which wraps Windows Core Audio — the same interface
# the taskbar volume slider uses, so changes here move that slider too.

from pycaw.pycaw import AudioUtilities


def _output_device():
    # Re-acquired on every call so we always talk to the *current* default
    # output device — it changes when e.g. a headset gets plugged in.
    return AudioUtilities.GetSpeakers()


def _input_device():
    # pycaw's GetMicrophone(), unlike GetSpeakers(), returns the raw COM
    # device without wrapping it — CreateDevice is the same wrapping step
    # GetSpeakers() does internally, needed here to get .FriendlyName and
    # .EndpointVolume on the mic too.
    return AudioUtilities.CreateDevice(AudioUtilities.GetMicrophone())


def get_output_device_name() -> str:
    return _output_device().FriendlyName


def get_input_device_name() -> str:
    return _input_device().FriendlyName


def get_percent() -> int:
    return round(_output_device().EndpointVolume.GetMasterVolumeLevelScalar() * 100)


def set_percent(value: int) -> int:
    value = max(0, min(100, value))
    _output_device().EndpointVolume.SetMasterVolumeLevelScalar(value / 100, None)
    return value


def change_percent(delta: int) -> int:
    return set_percent(get_percent() + delta)


def get_mic_percent() -> int:
    return round(_input_device().EndpointVolume.GetMasterVolumeLevelScalar() * 100)


def set_mic_percent(value: int) -> int:
    value = max(0, min(100, value))
    _input_device().EndpointVolume.SetMasterVolumeLevelScalar(value / 100, None)
    return value


def change_mic_percent(delta: int) -> int:
    return set_mic_percent(get_mic_percent() + delta)


def is_mic_muted() -> bool:
    return bool(_input_device().EndpointVolume.GetMute())


def toggle_mic_mute() -> bool:
    endpoint = _input_device().EndpointVolume
    muted = not endpoint.GetMute()
    endpoint.SetMute(muted, None)
    return muted
