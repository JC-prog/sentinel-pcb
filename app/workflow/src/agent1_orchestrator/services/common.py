from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class ServiceResult:
    success: bool
    status: str
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    recoverable: bool = False
    next_action: Optional[str] = None
