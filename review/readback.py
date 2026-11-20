from .register import ReportRegister, ReviewReceipt, ReviewReport
from .resample_readback import read_resample as read_resample


def read_v1(register: ReportRegister, report_id: int) -> tuple[ReviewReport, ReviewReceipt]:
    return register.load(report_id)
