from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderLevel:
    price: float
    size_usd: float


@dataclass(frozen=True)
class OrderBook:
    market_id: str
    yes_asks: list[OrderLevel]
    yes_bids: list[OrderLevel]


@dataclass(frozen=True)
class ExecutablePrice:
    average_price: float
    fillable_usd: float
    levels_used: int


@dataclass(frozen=True)
class EdgeCandidate:
    market_name: str
    market_id: str
    model_probability: float
    executable_price: float
    edge_pp: float
    ev_per_dollar: float
    fillable_usd: float
    side: str = "YES"


def executable_yes_price(book: OrderBook, target_usd: float) -> ExecutablePrice:
    return _executable_price(
        [(level.price, level.size_usd) for level in sorted(book.yes_asks, key=lambda item: item.price)],
        target_usd,
    )


def executable_no_price(book: OrderBook, target_usd: float) -> ExecutablePrice:
    no_levels: list[tuple[float, float]] = []
    for level in sorted(book.yes_bids, key=lambda item: item.price, reverse=True):
        if level.price <= 0 or level.price >= 1:
            continue
        no_price = 1.0 - level.price
        yes_shares_bid = level.size_usd / level.price
        no_notional_usd = yes_shares_bid * no_price
        no_levels.append((no_price, no_notional_usd))
    return _executable_price(no_levels, target_usd)


def _executable_price(levels: list[tuple[float, float]], target_usd: float) -> ExecutablePrice:
    if target_usd <= 0:
        raise ValueError("target_usd must be positive")
    remaining = float(target_usd)
    spent = 0.0
    shares_acquired = 0.0
    levels_used = 0
    for price, size_usd in levels:
        take = min(size_usd, remaining)
        if take <= 0:
            continue
        if price <= 0:
            continue
        shares_acquired += take / price
        spent += take
        remaining -= take
        levels_used += 1
        if remaining <= 1e-9:
            break
    if spent <= 0 or shares_acquired <= 0:
        return ExecutablePrice(average_price=float("nan"), fillable_usd=0.0, levels_used=0)
    return ExecutablePrice(
        average_price=round(spent / shares_acquired, 6),
        fillable_usd=round(spent, 6),
        levels_used=levels_used,
    )


def detect_yes_edge(
    market_name: str,
    model_probability: float,
    book: OrderBook,
    target_usd: float,
    min_edge_pp: float,
    fees_bps: float = 0.0,
) -> EdgeCandidate | None:
    executable = executable_yes_price(book, target_usd)
    if executable.fillable_usd <= 0:
        return None
    fee_multiplier = 1.0 + fees_bps / 10000.0
    price_after_fees = round(executable.average_price * fee_multiplier, 6)
    edge = float(model_probability) - price_after_fees
    edge_pp = edge * 100.0
    if edge_pp < min_edge_pp:
        return None
    ev_per_dollar = (float(model_probability) / price_after_fees) - 1.0
    return EdgeCandidate(
        market_name=market_name,
        market_id=book.market_id,
        model_probability=float(model_probability),
        executable_price=price_after_fees,
        edge_pp=edge_pp,
        ev_per_dollar=ev_per_dollar,
        fillable_usd=executable.fillable_usd,
        side="YES",
    )


def detect_no_edge(
    market_name: str,
    model_yes_probability: float,
    book: OrderBook,
    target_usd: float,
    min_edge_pp: float,
    fees_bps: float = 0.0,
) -> EdgeCandidate | None:
    executable = executable_no_price(book, target_usd)
    if executable.fillable_usd <= 0:
        return None
    model_no_probability = 1.0 - float(model_yes_probability)
    fee_multiplier = 1.0 + fees_bps / 10000.0
    price_after_fees = round(executable.average_price * fee_multiplier, 6)
    edge = model_no_probability - price_after_fees
    edge_pp = edge * 100.0
    if edge_pp < min_edge_pp:
        return None
    ev_per_dollar = (model_no_probability / price_after_fees) - 1.0
    return EdgeCandidate(
        market_name=market_name,
        market_id=book.market_id,
        model_probability=model_no_probability,
        executable_price=price_after_fees,
        edge_pp=edge_pp,
        ev_per_dollar=ev_per_dollar,
        fillable_usd=executable.fillable_usd,
        side="NO",
    )


def detect_edges(
    model_probabilities: dict[str, float],
    order_books: dict[str, OrderBook],
    target_usd: float,
    min_edge_pp: float,
    fees_bps: float = 0.0,
    include_no: bool = False,
) -> list[EdgeCandidate]:
    candidates: list[EdgeCandidate] = []
    for market_name, probability in model_probabilities.items():
        book = order_books.get(market_name)
        if book is None:
            continue
        candidate = detect_yes_edge(market_name, probability, book, target_usd, min_edge_pp, fees_bps)
        if candidate is not None:
            candidates.append(candidate)
        if include_no:
            no_candidate = detect_no_edge(
                market_name,
                probability,
                book,
                target_usd,
                min_edge_pp,
                fees_bps,
            )
            if no_candidate is not None:
                candidates.append(no_candidate)
    return sorted(candidates, key=lambda item: item.edge_pp, reverse=True)
