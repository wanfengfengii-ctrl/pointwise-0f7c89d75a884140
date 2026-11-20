from .resample_register import ResampleRegister, load_resample


def read_resample(register, plan_id: int) -> tuple[dict, dict]:
    connection = getattr(register, 'connection', None)
    if connection is None:
        raise ValueError('a center register with a live connection is required')
    return load_resample(connection, plan_id)
