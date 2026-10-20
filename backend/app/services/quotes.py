from app.services.broker import Broker, BrokerError


def price_map(broker: Broker) -> dict[str, float] | None:
    try:
        positions = broker.get_positions()
    except BrokerError:
        return None
    return {item["symbol"]: item["current_price"] for item in positions}
