"""Event bus for application-wide messaging."""
from typing import Callable, Dict, List, Any

class EventBus:
    """Simple event bus for pub/sub messaging."""
    
    def __init__(self):
        """Initialize event bus."""
        self._subscribers: Dict[str, List[Callable]] = {}
    
    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Subscribe to an event."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
    
    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """Unsubscribe from an event."""
        if event_type in self._subscribers:
            self._subscribers[event_type].remove(handler)
    
    def emit(self, event_type: str, *args, **kwargs) -> None:
        """Emit an event to all subscribers."""
        if event_type in self._subscribers:
            for handler in self._subscribers[event_type]:
                handler(*args, **kwargs)
