from pydantic import BaseModel
from datetime import datetime


class User(BaseModel):
    id: int
    user_name: str
    wallet_addr: str | None = None
    verified: bool = False


class Token(BaseModel):
    id: int
    symbol: str
    name: str
    rank: int
    mcap: int | None = None
    type = "cmc_int_id_v0"      # if in the future, we need to use a different token api, can change this


class BetProposal(BaseModel):
    id: int
    chat_created_in: int
    created_at: datetime
    valid_till: datetime
    created_by: int
    counterparty: int | None    # None is implicitly "Anyone in same chat"
    creator_over: bool          # true -> over_addr = created_by, false -> vice versa
    amount: int                 # in WEI
    str_exp: str                # string representation of expiry
    expiry: int                 # block number
    price: int                  # uint256
    token: Token


class Bet(BaseModel):
    id: int
    chat_created_in: int
    created_at: int                     # unix time
    over_user_id: int
    under_user_id: int
    amount: str                         # in WEI, string to avoid overflow
    expiry: int                         # block number
    price: str                          # same as amount
    token_type = "cmc_int_id_v0"        # how to resolve token string on contract to an actual price
    token: str
    creation_hash: str


class BetList(BaseModel):
    active: list[Bet]
    pending: list[BetProposal]


class RequestBetResponse(BaseModel):
    success: bool
    error_msg: str | None
    bet_proposal: BetProposal | None = None


class AcceptBetResponse(BaseModel):
    success: bool
    error_msg: str | None
    tx_hash: str | None = None
    bet: Bet | None = None


# whatever, fuck it, two of them!
class SettleBetResponse(BaseModel):
    success: bool
    bet: Bet
    error_msg: str | None
    success_msg: str | None = None
    tx_hash: str | None = None
