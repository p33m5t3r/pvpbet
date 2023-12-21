from .schema import *
from web3 import Web3
from hexbytes import HexBytes
from eth_typing import Address
import requests
import json
from json import JSONDecodeError
import os
from datetime import datetime, timedelta
from eth_account.messages import encode_defunct
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import heapq
from pydantic import ValidationError
import logging
from web3.exceptions import ContractLogicError, ABIFunctionNotFound, MismatchedABI

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Configure logger
# c_handler = logging.StreamHandler()
# f_handler = logging.FileHandler('tgbot_backend_api.log')
# c_handler.setLevel(logging.DEBUG)
# f_handler.setLevel(logging.WARNING)
#
# c_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# f_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# c_handler.setFormatter(c_format)
# f_handler.setFormatter(f_format)
#
# logger.addHandler(c_handler)
# logger.addHandler(f_handler)


# using priority queue to store bets because I care about efficient access to the next expiring bet
# since I will be checking it every few blocks, but I don't care about efficient access to all bets
class InMemoryBetDb:
    def __init__(self):
        self._queue = []
        self._index = 0

    def is_empty(self):
        return not self._queue

    def peek(self):
        if self.is_empty():
            return None
        return self._queue[0][0]

    def push(self, bet: Bet):
        heapq.heappush(self._queue, (bet.expiry, self._index, bet))
        self._index += 1

    def pop(self):
        return heapq.heappop(self._queue)[-1]

    def get_bets_by_user_id(self, user_id: int) -> list[Bet] | None:
        return [i[-1] for i in self._queue if i[-1].over_user_id == user_id or i[-1].under_user_id == user_id]

    def get_bets_by_chat_id(self, chat_id: int) -> list[Bet] | None:
        return [item[-1] for item in self._queue if item[-1].chat_created_in == chat_id]

    def __repr__(self):
        items = [item[-1] for item in self._queue]
        return f"PriorityQueue({items})"

    def __len__(self):
        return len(self._queue)


class ApiV2:
    def __init__(self, contract_addr: str, rpc_url: str, pk: str, l1_rpc_url: str):

        client = MongoClient('localhost', 27017)
        db = client['database']
        self.user_db = db.users
        self.user_db.create_index("id", unique=True)

        logger.info(f"loaded {len(list(self.user_db.find({})))} users from database.")

        self.active_bets_db = db.active_bets
        logger.info("Loading in-memory bet cache from database...")
        self.bet_cache = InMemoryBetDb()
        for bet in self.active_bets_db.find():
            logger.info(f"loading bet: {bet}")
            _bet = Bet(**bet)
            self.bet_cache.push(_bet)
        logger.info(f"Done: loaded {len(self.bet_cache)} bets from database.")
        self.pending_bets = []                          # TODO: k: id, v: BetProposal?


        self.RPC_URL = rpc_url
        self.l1_RPC_URL = l1_rpc_url
        self.accept_gas = 400_000
        self.deposit_gas = 400_000
        self.settle_gas = 150_000

        if self.RPC_URL.__contains__("arbitrum"):
            self.accept_gas = self.deposit_gas = self.settle_gas = 1_000_000


        w3 = Web3(Web3.HTTPProvider(self.RPC_URL))
        if w3 is None or not w3.is_connected():
            raise ConnectionError("Failed to connect to web3 provider.")

        self.w3 = w3
        self.account = self.w3.eth.account.from_key(pk)
        contract_addr = Address(bytes.fromhex(contract_addr[2:]))

        try:
            abi_path = os.path.join("/".join(os.getcwd().split('/')[:-1]), "bookie/out/Bookie.sol/bookie.json")
            with open(abi_path, "r") as file:
                abi = json.loads(file.read()).get('abi')
        except FileNotFoundError:
            abi_path = os.path.join("/".join(os.getcwd().split('/')), "bookie/out/Bookie.sol/bookie.json")
            with open(abi_path, "r") as file:
                abi = json.loads(file.read()).get('abi')

        self.contract_instance = self.w3.eth.contract(address=contract_addr, abi=abi)

        def try_setup_call(fn):
            try:
                return fn.call()
            except (ContractLogicError, ABIFunctionNotFound, MismatchedABI):
                logger.critical("failed to call contract function required for proper setup")
                raise

        self.block_safety_margin = try_setup_call(self.contract_instance.functions.BLOCK_SAFETY_MARGIN())
        self.max_bet_size = try_setup_call(self.contract_instance.functions.max_bet_size())
        self.max_account_balance = try_setup_call(self.contract_instance.functions.max_account_balance())
        self.release_version = try_setup_call(self.contract_instance.functions.RELEASE_VERSION())

        # only used when user sizes a bet in dollars, doesn't need to be updated often/be super accurate
        # if settling a bet, this is NOT referenced; the coinmarketcap API is used instead
        self.eth_price = 1800
        self.eth_price_last_update = datetime.now()
        self.eth_price_cache_duration = timedelta(minutes=60)

        self.cmc_base_url = "https://pro-api.coinmarketcap.com"
        self.cmc_headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": "TODO_ADD_API_KEY"}
        logger.info("ApiV2 initialized.")

    @staticmethod
    def get_txn_error(rpc_url: str, tx_hash: str):
        headers = {"Content-Type": "application/json"}
        data = {
            "jsonrpc": "2.0",
            "method": "debug_traceTransaction",
            "params": [tx_hash],
            "id": 1,
        }
        response = requests.post(rpc_url, headers=headers, data=json.dumps(data))
        try:
            return_value = response.json().get('result').get('returnValue')
            error_hex = return_value[138:]   # strip irrelevant bytes. TODO: make this more robust
            return bytes.fromhex(error_hex).decode('utf-8').rstrip('\0')
        except (AttributeError, ValueError):
            return None


    @staticmethod
    def to_eth(n: int) -> float:
        return n / 1000000000000000000

    @staticmethod
    def addr_from_str(s: str):
        try:
            return Address(bytes.fromhex(s)) if "x" not in s else Address(bytes.fromhex(s[2:]))
        except ValueError:
            print("invalid address!")
            return None

    # converts a string like "10d" or "5m" to a timedelta object
    @staticmethod
    def parse_duration_string(duration_string) -> timedelta:
        unit = duration_string[-1]
        value = int(duration_string[:-1])

        if unit == 'm':
            duration = timedelta(minutes=value)
        elif unit == 'h':
            duration = timedelta(hours=value)
        elif unit == 'd':
            duration = timedelta(days=value)
        else:
            raise ValueError("Invalid duration unit. Please use 'm', 'h', or 'd'.")

        return duration

    @staticmethod
    def parse_number_from_str(string: str) -> float:
        decimal_chars = '0123456789.'
        decimal_string = ''.join(char for char in string if char in decimal_chars)
        decimal_value = float(decimal_string)
        return decimal_value

    # converts a date-like expression into a block number
    @staticmethod
    def parse_date_expr(current_block: int, _timedelta: str) -> int:
        _mins = _timedelta[-2:] == "mo"
        if _mins:
            value = int(_timedelta[:-2])
            unit = "mo"
        else:
            value = int(_timedelta[:-1])  # Extract the numeric value
            unit = _timedelta[-1].lower()  # Extract the unit (h, d, m)

        if unit == 'm':
            seconds = value * 60
        elif unit == 'h':
            seconds = value * 3600
        elif unit == 'd':
            seconds = value * 86400
        elif unit == 'mo':
            seconds = value * 2_635_200
        else:
            raise ValueError("Invalid duration unit. Please use 'h', 'd', or 'm'.")

        blocks = seconds // 12
        target_block = current_block + blocks

        return target_block

    # returns value in wei from string that looks like "$10" or "10$" or "0.1234E" or "10ETH"
    @staticmethod
    def parse_amt_expr(expr: str, eth_price: float) -> int:
        if expr.__contains__("$"):
            if expr[0] == '$':
                value = expr[1:]
            else:
                value = ApiV2.parse_number_from_str(expr)
            return Web3.to_wei(float(value) / eth_price, 'ether')
        else:
            value = ApiV2.parse_number_from_str(expr)
            return Web3.to_wei(value, 'ether')

    @staticmethod
    def parse_cmc_token_data(raw_token: dict) -> Token | None:
        _id = raw_token.get('id')
        _symbol = raw_token.get('symbol')
        _name = raw_token.get('name')
        _rank = raw_token.get('cmc_rank')
        if _id is None or _symbol is None or _name is None or _rank is None:
            print(f"couldn't fetch token info! (token={raw_token}) skipping...")
            return None

        _market_cap = None
        if raw_token.get('self_reported_market_cap') is not None:
            _market_cap = raw_token.get('self_reported_market_cap')
        else:
            _price = raw_token.get('quote').get('USD').get('price')
            _circulating_supply = raw_token.get('circulating_supply')
            if _circulating_supply is not None and _circulating_supply != 0:
                _market_cap = _price * _circulating_supply
            else:
                _circulating_supply = raw_token.get('self_reported_circulating_supply')
                if _circulating_supply is not None and _circulating_supply != 0:
                    _market_cap = _price * _circulating_supply
        _token = None
        try:
            if _market_cap:
                _market_cap = int(_market_cap)
            _token = Token(id=_id, symbol=_symbol, name=_name, rank=_rank, mcap=_market_cap)
        except ValidationError:
            print(f"couldn't validate token (id={_id}, name={_name}, symbol={_symbol})!")

        return _token

    def get_l1_block_number(self):
        base_url = self.l1_RPC_URL
        headers = {"Content-Type": "application/json"}
        data = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
        response = requests.post(base_url, headers=headers, data=json.dumps(data))
        if response.ok:
            return int(response.json().get('result'), 16)
        else:
            return None

    # returns a list of possible tokens from an ambiguous slug like "SUI"
    def get_tokens_from_expr(self, expr: str) -> list[Token] | None:
        logger.debug(f"getting tokens from expr: {expr}")
        _headers = self.cmc_headers

        # first, try by slug (full name in the cmc url). If it works, just return that token in a single-element list
        _params = {"slug": expr.lower()}
        response = requests.get(f"{self.cmc_base_url}/v2/cryptocurrency/quotes/latest",
                                headers=_headers, params=_params)
        if response.ok:
            data = response.json()
            if data.get('status').get('error_code') == 0:
                if len(data.get('data')) == 1:
                    raw_token = list(data.get('data').values())[0]
                    token = self.parse_cmc_token_data(raw_token)
                    if token:
                        return [token]
        else:
            logger.debug("slug match failed, trying symbol match...")

        # if that didn't work, for whatever reason, try by symbol, and return a
        # ... list of possible matches to handle client-side
        _params = {"symbol": expr}
        response = requests.get(f"{self.cmc_base_url}/v2/cryptocurrency/quotes/latest",
                                headers=_headers, params=_params)
        if not response.ok:
            logger.error("requests error")
            return None

        data = response.json()
        if data.get('status').get('error_code') != 0:
            logger.warning(f"cmc api returned error: {data.get('status').get('error_message')}")

        tokens = []
        raw_list = data.get('data').get(expr)
        if raw_list is None:
            raw_list = data.get('data').get(expr.upper())
        if raw_list is None:
            raw_list = data.get('data').get(expr.lower())
        if raw_list is None:
            logger.warning(f"cmc api returned no data for expr: {expr}")
            return None
        for raw_token in raw_list:
            token = self.parse_cmc_token_data(raw_token)
            if token:
                tokens.append(token)

        logger.debug(f"found {len(tokens)} tokens: {tokens}")
        return tokens

    # returns a token object from an ID, assumes exact match
    # TODO: store tokens in db and try that first
    def get_token_by_id(self, _id: int) -> Token | None:
        _headers = self.cmc_headers
        _params = {"id": _id}
        response = requests.get(f"{self.cmc_base_url}/v2/cryptocurrency/quotes/latest",
                                headers=_headers, params=_params)
        return self.parse_cmc_token_data(response.json().get('data').get(str(_id)))

    def get_token_price(self, tkn: Token) -> float | None:
        _headers = self.cmc_headers
        _params = {"id": tkn.id}
        response = requests.get(f"{self.cmc_base_url}/v2/cryptocurrency/quotes/latest",
                                headers=_headers, params=_params)
        try:
            return float(response.json().get('data').get(str(tkn.id)).get('quote').get('USD').get('price'))
        except (AttributeError, JSONDecodeError, TypeError):
            logger.error(f"couldn't fetch token price! (token={tkn})")
            return None

    # validates a bet proposal and adds it to the pending bets list
    def request_bet(self, chat_id: int, user_id: int, over: bool, offer_valid_till: str,
                    value_expr: str, bet_expiration: str, price: float,
                    token: Token, counterparty=None) -> RequestBetResponse:
        logger.info(f"requesting bet with params: {locals()}")
        # check if eth price is stale, if so, update; in case request is in dollars
        current_time = datetime.now()
        if current_time - self.eth_price_last_update > self.eth_price_cache_duration:
            try:
                logger.debug("updating eth price")
                req = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd")
                self.eth_price = float(req.json().get('ethereum').get('usd'))
            except (AttributeError, JSONDecodeError, TypeError):
                logger.warning(f"failed to update eth price, using old value of ${self.eth_price}")

        # CHECKS:
        # -1. user needs to have a wallet and be verified
        _user = self.get_user_by_id(user_id)
        if _user is None:
            return RequestBetResponse(success=False, error_msg="you need to run /setup <wallet addr> first!")
        if _user.wallet_addr is None:
            return RequestBetResponse(success=False, error_msg="you need to run /setup <wallet addr> first!")
        if not _user.verified:
            return RequestBetResponse(success=False, error_msg="you need to run /verify <signature> first!")

        # 0. counterparty needs to be valid, if it was specified by the caller
        _counterparty_id = None
        if counterparty is not None:
            try:
                _counterparty_user = self.get_user_by_username(counterparty)
                if _counterparty_user is None:
                    return RequestBetResponse(success=False, error_msg="invalid counterparty!")
                _counterparty_id = _counterparty_user.id
            except ValidationError:
                return RequestBetResponse(success=False, error_msg="invalid counterparty!")
            if _counterparty_user.wallet_addr is None:
                return RequestBetResponse(success=False, error_msg="counterparty hasn't set up their wallet yet.")
            if not _counterparty_user.verified:
                return RequestBetResponse(success=False, error_msg="counterparty hasn't verified their wallet yet.")

        # 1. token needs to be valid
        if token is None or type(token) is not Token:
            return RequestBetResponse(success=False, error_msg="invalid token!")

        # 2. bet offer duration needs to be valid
        try:
            _created_at = datetime.now()
            _timedelta = self.parse_duration_string(offer_valid_till)
            _valid_till = (_created_at + _timedelta)
        except ValueError:
            return RequestBetResponse(success=False, error_msg="invalid offer duration!")

        if _valid_till < _created_at:
            return RequestBetResponse(success=False, error_msg="offer duration must be in the future!")

        # 3. bet expiration needs to be valid
        current_block = self.get_l1_block_number()
        if current_block is None:
            logger.error("failed to fetch current block number!")
            return RequestBetResponse(success=False, error_msg="failed to fetch current block number!")
        try:
            block_exp = self.parse_date_expr(current_block, bet_expiration)
        except ValueError:
            return RequestBetResponse(success=False, error_msg="invalid bet expiration!")

        if block_exp < current_block:
            return RequestBetResponse(success=False, error_msg="bet expiration must be in the future!")

        if block_exp < current_block + self.block_safety_margin:
            return RequestBetResponse(success=False, error_msg="bet expires too soon! (try a later time)")

        # 4. price needs to be valid
        try:
            _price = self.w3.to_wei(price, 'ether')  # all token prices are treated like eth to store uint in contract
        except (ValueError, TypeError):
            return RequestBetResponse(success=False, error_msg="invalid price!")

        # 5. quantity wagered ("value") needs to be valid
        try:
            amt_wei = self.parse_amt_expr(value_expr, self.eth_price)
        except (ValueError, TypeError):
            return RequestBetResponse(success=False, error_msg="invalid wager amount!")

        if amt_wei <= 0:
            return RequestBetResponse(success=False, error_msg="wager amount must be positive!")

        if amt_wei > self.w3.eth.get_balance(self.addr_from_str(_user.wallet_addr)):
            return RequestBetResponse(success=False, error_msg="you don't have enough eth to cover this bet!")

        if amt_wei > self.max_bet_size:
            return RequestBetResponse(success=False, error_msg=f"bet size too large! (max={self.max_bet_size})")

        # get the next available id
        current_ids = set([bet.id for bet in self.pending_bets])
        smol_numbers = set(range(0, 100_000))   # TODO: this is a hack, fix this
        _id = min(list(smol_numbers - current_ids))

        try:
            _bet_prop = BetProposal(id=_id, chat_created_in=chat_id, created_at=_created_at, valid_till=_valid_till,
                                    created_by=user_id, counterparty=_counterparty_id, creator_over=over, amount=amt_wei,
                                    expiry=block_exp, price=_price, token=token, str_exp=bet_expiration)
        except ValidationError as e:
            logger.error(f"failed to instantiate a bet proposal: pydantic validation error: {e}")
            return RequestBetResponse(success=False, error_msg="unknown validation error ): "
                                                               "it's probably not your fault")

        self.pending_bets.append(_bet_prop)
        logger.info(f"successfully added bet proposal: {_bet_prop}")
        return RequestBetResponse(success=True, bet_proposal=_bet_prop, error_msg=None)

    # removes a bet request from pending list and returns True if successful
    def rm_bet_request(self, bet_id: int):
        try:
            for bet in self.pending_bets:
                if bet.id == bet_id:
                    self.pending_bets.remove(bet)
                    logger.info(f"successfully removed bet proposal: {bet}")
            return True
        except IndexError:
            logger.critical("FAILED TO DROP PENDING BET REQUEST")
            return False

    def get_bet_proposals_by_chat_id(self, chat_id: int) -> list[BetProposal]:
        return [bet for bet in self.pending_bets if bet.chat_created_in == chat_id]

    def get_bet_proposal_by_id(self, bet_id: int) -> BetProposal | None:
        try:
            return [bet for bet in self.pending_bets if bet.id == bet_id][0]
        except IndexError:
            return None

    # side effect: purges expired bets!!!!!
    def get_bets_by_chat_id(self, chat_id: int) -> BetList:
        active_bets = self.bet_cache.get_bets_by_chat_id(chat_id)
        pending_bets = self.get_bet_proposals_by_chat_id(chat_id)
        for bet in pending_bets:
            if bet.valid_till < datetime.now():
                logger.info(f"purged expired bet proposal: id: {bet.id}")
                self.rm_bet_request(bet.id)

        return BetList(active=active_bets, pending=pending_bets)

    # side effect: purges expired bets!!!!!
    def get_bets_by_user_id(self, user_id: int) -> BetList:
        active_bets = self.bet_cache.get_bets_by_user_id(user_id)
        pending_bets_by_user = [bet for bet in self.pending_bets if bet.created_by == user_id]
        _found_bet_ids = [bet.id for bet in pending_bets_by_user]
        pending_bets_by_counterparty = [bet for bet in self.pending_bets
                                        if bet.counterparty is not None and bet.counterparty == user_id
                                        and bet.id not in _found_bet_ids]
        pending_bets = pending_bets_by_user + pending_bets_by_counterparty
        for bet in pending_bets:
            if bet.valid_till < datetime.now():
                logger.info(f"purged expired bet proposal: id: {bet.id}")
                self.rm_bet_request(bet.id)

        return BetList(active=active_bets, pending=pending_bets)

    # TODO: add a change wallet command
    # returns user true iff created new user, and message to send to user
    def create_unverified_user(self, new_user: User) -> (bool, str):
        existing_user = self.user_db.find_one({"id": new_user.id})
        if existing_user is None:
            try:
                res = self.user_db.insert_one(new_user.dict())
                logger.info(f"created unverified user {new_user.user_name} with id {res.inserted_id}")
                _text_1 = "Congrats! We've added you to the system. Follow this link to verify your account:\n"
                _text_2 = f"https://pvpbet.vercel.app/?{new_user.user_name}"
                return True, _text_1 + _text_2
            except DuplicateKeyError:
                logger.error("failed to create unverified user: duplicate key error (this should never happen)")
                return False, "unknown error, please try again ):"
        else:
            current_wallet_addr = existing_user.get('wallet_addr')
            if current_wallet_addr is not None and current_wallet_addr != new_user.wallet_addr:
                logger.info(f"user {new_user.id} requested new wallet addr f{new_user.wallet_addr}")
                _text_1 = "You already have an account, but you've requested to change your wallet address.\n"
                _text_2 = f"current address: ({existing_user.get('wallet_addr')})\n"
                _text_3 = f"new address: ({new_user.wallet_addr})\n"
                _text_4 = f"If you want to change your address, please run /deactivate (/newwallet coming soon!)\n"
                return False, _text_1 + _text_2 + _text_3 + _text_4
            logger.info(f"user {new_user.user_name} with id {new_user.id} already exists")
            return False, "you've already setup this account!"

    # removes user from db and returns success/err msg
    def deactivate_user_by_id(self, user_id: int) -> str:
        try:
            res = self.user_db.delete_one({"id": user_id})
            if res.deleted_count == 0:
                logger.info("user tried deleting nonexistent account")
                return "You don't have an account yet! Run /setup to create one"
            else:
                logger.info(f"successfully removed user with id {user_id}")
                return "Successfully deactivated your account!"
        except IndexError:
            logger.error(f"failed to remove user with id {user_id}")
            return "Something went wrong, please try again later ):"

    # returns true if successful, false otherwise, and derived wallet address
    def validate_signature(self, msg: str, wallet_addr: str, signature: str) -> (bool, str):
        msg = encode_defunct(text=msg)
        derived_addr = self.w3.eth.account.recover_message(msg, signature=HexBytes(signature))
        return derived_addr == wallet_addr, derived_addr

    # returns true if successful, false otherwise, and success/fail message
    def verify_user_by_id(self, user_id: int, signature: str) -> (bool, str):
        query = self.user_db.find_one({"id": user_id})
        new_user = User(**query)
        existing_signature = self.user_db.find_one({"wallet_addr": new_user.wallet_addr, "verified": True})
        if existing_signature is not None:
            logger.debug("user already verified")
            return False, "You're already verified!"
        msg = f"authorize {new_user.user_name} pvpbet v0"
        _success, _derived = self.validate_signature(msg, new_user.wallet_addr, signature)
        if _success:
            query = {"id": user_id}
            new_values = {"$set": {"verified": True}}
            self.user_db.update_one(query, new_values)
            logger.debug(f"verified user {new_user.user_name} with id {user_id}, updated db record")
            return True, "🤑 Congrats! We've verified your wallet; you're all ready to start betting! 🤑"
        else:
            logger.debug("signature verification failed (this may be intended behavior)")
            _msg_1 = "We couldn't verify your signature. Please try again.\n"
            _msg_2 = f"(hint: it looks like you signed with the wallet address {_derived}). Is this correct?"
            return False, _msg_1 + _msg_2

    def get_user_by_id(self, user_id: int):
        if type(user_id) != int:
            _err_msg = "CRITICAL: user_id is not an int, you are passing in a raw user object, which is not allowed"
            raise TypeError(_err_msg)
        query = self.user_db.find_one({"id": user_id})
        if query is None:
            return None
        try:
            return User(**query)
        except ValidationError:
            logger.warning(f"failed to get user with id {user_id} from db")
            return None

    def get_user_by_username(self, user_name: str):
        if type(user_name) is int:
            logger.error("someone called get_username with an integer...")
            return None
        if user_name[0] == "@":
            user_name = user_name[1:]
        query = self.user_db.find_one({"user_name": user_name})
        try:
            return User(**query)
        except (ValidationError, TypeError):
            logger.warning(f"failed to get user with username {user_name} from db")
            return None

    # returns available, locked in WEI
    def get_user_balance_by_id(self, user_id: int):
        _user_addr = self.get_user_by_id(user_id).wallet_addr
        _avail = self.contract_instance.functions.getSpendableBalance(_user_addr).call()
        _locked = self.contract_instance.functions.getLockedBalance(_user_addr).call()

        return _avail, _locked

    def transact(self, transaction, _account):
        logger.info(f"requesting txn signature with account {_account.address}")
        nonce = self.w3.eth.get_transaction_count(_account.address)
        # gas_estimate = self.contract_instance.functions.deposit().estimate_gas()
        # print(gas_estimate) # danger: will fail txn if called like this with no deposit value!
        transaction.update({"nonce": nonce})

        signed_txn = _account.sign_transaction(transaction)
        result = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)

        tx_receipt = self.w3.eth.wait_for_transaction_receipt(result)
        return tx_receipt

    # def get_bet_count(self):
    #     return self.contract_instance.functions.bet_count().call()

    # def get_bet(self, _id: int):
    #     return self.contract_instance.functions.getBet(abs(_id)).call()

    # for tests
    def test_deposit(self, account=None):
        _account = account if account is not None else self.account

        transaction = self.contract_instance.functions.deposit().build_transaction({
            'value': self.w3.to_wei(1, 'ether'),
            'gas': self.deposit_gas,
            'gasPrice': self.w3.to_wei('1', 'gwei')
        })

        tx_receipt = self.transact(transaction, _account)
        tx_hash = tx_receipt.get("transactionHash").hex()
        _err = self.get_txn_error(self.RPC_URL, tx_hash)
        if _err is not None:
            print(f"txn errored with: {_err}")
            logger.debug(f"txn errored with: {_err}")


    # TODO: move this underneath the request_bet method
    def accept_bet(self, caller_id: int, chat_id: int, bet_id: int) -> AcceptBetResponse:
        # first, try and resolve the caller from the users database
        try:
            caller = self.get_user_by_id(caller_id)
        except ValidationError:
            return AcceptBetResponse(success=False, error_msg="invalid user id")

        # then, get the bet request from the pending list:
        bet_req = self.get_bet_proposal_by_id(bet_id)
        if bet_req is None:
            return AcceptBetResponse(success=False, error_msg="invalid bet id, try running \"/bets offered\""
                                                              "to see valid offers")

        # next, run checks. if error, return error
        # TODO: check if bet was already accepted
        # 0. check if chat is correct
        if bet_req.chat_created_in != chat_id:
            _msg = f"this bet (id:{bet_req.id}) wasn't offered in this chat!"
            return AcceptBetResponse(success=False, error_msg=_msg)

        # 1. if counterparty specified, make sure it's the caller, and if it's open to anyone, set it to the caller:
        if bet_req.counterparty is not None:
            if bet_req.counterparty != caller_id:
                _msg = f"this bet (id:{bet_req.id}) wasn't offered to you!"
                return AcceptBetResponse(success=False, error_msg=_msg)
        else:
            bet_req.counterparty = caller_id

        # 2. the offer is still valid (and remove the bet from pending if it's not):
        if bet_req.valid_till < datetime.now():
            self.rm_bet_request(bet_req.id)
            _msg = f"this bet offer (id:{bet_req.id}) has expired! (removed from list of open offers)"
            return AcceptBetResponse(success=False, error_msg=_msg)

        # 3. the caller has enough funds to accept the bet:
        _avail, _locked = self.get_user_balance_by_id(caller_id)
        if self.w3.to_wei(_avail, 'ether') < bet_req.amount:
            _msg_1 = f"insufficient funds! (id:{bet_req.id})"
            _msg_2 = f"funds available: {round(self.to_eth(_avail), 4)}"
            _msg_3 = f"funds required: {round(self.to_eth(bet_req.amount), 4)}"
            return AcceptBetResponse(success=False, error_msg="\n".join((_msg_1, _msg_2, _msg_3)))

        # 4. the caller has a verified wallet:
        if not caller.verified:
            _msg = f"you need to verify your wallet before you can accept bets!"
            return AcceptBetResponse(success=False, error_msg=_msg)

        # once checks are passed, form the transaction and send it (abi fn signature below for reference)
        # fn makeBet(address _over, address _under, string calldata _sym, uint256 _amt, uint256 _price, uint256 _exp)
        _over_user_id = bet_req.created_by if bet_req.creator_over else bet_req.counterparty
        _under_user_id = bet_req.counterparty if bet_req.creator_over else bet_req.created_by
        try:
            _over = self.get_user_by_id(_over_user_id).wallet_addr
        except ValidationError:
            _msg = f"error: failed to find wallet addr for user {_over_user_id}"
            return AcceptBetResponse(success=False, error_msg=_msg)
        try:
            _under = self.get_user_by_id(_under_user_id).wallet_addr
        except ValidationError:
            _msg = f"error: failed to find wallet addr for user {_under_user_id}"
            return AcceptBetResponse(success=False, error_msg=_msg)

        _token = str(bet_req.token.id)
        _amt = bet_req.amount
        _price = bet_req.price
        _exp = bet_req.expiry

        txn = self.contract_instance.functions.makeBet(_over, _under, _token, _amt, _price, _exp).build_transaction({
            'from': self.account.address,
            'gas': self.accept_gas,         # max gas used in tests was ~250k, but since gas is free on l2, fuck it
            'gasPrice': self.w3.to_wei('3', 'gwei')
        })

        # remove the bet from the pending list *before* sending the txn
        # this way, if pending list de-sync's with some weird runtime error,
        # it's only missing pending bets rather than having duplicates
        if not self.rm_bet_request(bet_req.id):
            return AcceptBetResponse(success=False, error_msg="unknown error, couldn't remove bet from pending list")

        # TODO: signing server signing server signing server!
        tx_receipt = self.transact(txn, self.account)
        tx_hash = tx_receipt.get('transactionHash').hex()
        _bet_id = None
        # if the txn succeeds, we have to do a bunch of bookkeeping
        if tx_receipt.status:
            _unix_time = int(bet_req.created_at.timestamp())
            # TODO: low-prio get rest of info from the emitted event
            # _bet_id will always be unique because it's coming straight from the contract
            _bet_id = int(tx_receipt.get('logs')[0].get('data')[:32].hex(), 16)
            bet = Bet(id=_bet_id, chat_created_in=chat_id, created_at=_unix_time, over_user_id=_over_user_id,
                      under_user_id=_under_user_id, token=_token, amount=str(_amt), price=str(_price), expiry=_exp,
                      creation_hash=tx_hash)

            # add the bet to the database and in-memory list
            self.active_bets_db.insert_one(bet.dict())
            self.bet_cache.push(bet)

            return AcceptBetResponse(success=True, tx_hash=tx_hash, bet=bet, error_msg=None)
        else:
            # add the bet back to the pending list if the txn fails
            self.pending_bets.append(bet_req)
            _revert_msg = self.get_txn_error(self.RPC_URL, tx_hash)
            _deleted_req_msg = ""
            if _revert_msg is None:
                _revert_msg = "unknown error"
            else:
                if _revert_msg == "bet expiration too soon":
                    removed = self.rm_bet_request(bet_req.id)
                    if removed:
                        _deleted_req_msg = "(removed from list of open offers)"
                    else:
                        logger.warning("couldn't remove bet from pending list, hushing notification to user")
            logger.warning(f"txn failed! tx_hash: {tx_hash}. reason={_revert_msg}")
            _msg = f"transaction failed: {_revert_msg} {_deleted_req_msg}"
            return AcceptBetResponse(success=False, tx_hash=tx_hash, error_msg=_msg)

    def settle_bet(self, bet: Bet) -> SettleBetResponse:
        # function settleBet(uint256 bet_id, bool over_wins) public onlyBookie {
        bet_price = self.to_eth(int(bet.price))         # to_eth just converts from 1e18, this unit is in $
        token_type = bet.token_type

        if token_type != "cmc_int_id_v0":
            # currently only one resolver hence return early, in the future, can use elif/match
            logger.error("invalid token price resolver invoked!")
            return SettleBetResponse(success=False, error_msg="Invalid token type: No matching resolver!")

        # TODO: (very low prio) if token id refuses to match after N tries, invalidate the bet
        # low prio because bettors can just mutually agree to invalidate as per the contract
        _token = self.get_token_by_id(int(bet.token))
        if _token is None:
            logger.warning("get_token_by_id returned None!")
            return SettleBetResponse(success=False, error_msg="Invalid token id: No matching token!")
        current_price = self.get_token_price(_token)
        if current_price is None:
            logger.warning("get_token_price returned None!")
            return SettleBetResponse(success=False, error_msg=f"error resolving price for token: {_token}")

        _over_wins = False
        if bet_price < current_price:
            _over_wins = True

        txn = self.contract_instance.functions.settleBet(bet.id, _over_wins).build_transaction({
            'from': self.account.address,
            'gas': self.settle_gas,                     # settling took max 40k in tests, but I really want to be cautious
            'gasPrice': self.w3.to_wei('3', 'gwei')
        })
        tx_receipt = self.transact(txn, self.account)
        tx_hash = tx_receipt.get('transactionHash').hex()
        if tx_receipt.status:
            # format the message to be delivered
            _winner_id = bet.over_user_id if _over_wins else bet.under_user_id
            _winner_name = self.get_user_by_id(_winner_id).user_name
            _loser_id = bet.under_user_id if _over_wins else bet.over_user_id
            _loser_name = self.get_user_by_id(_loser_id).user_name
            _winner_side = "over" if _over_wins else "under"
            _timestamp = datetime.fromtimestamp(bet.created_at).strftime("%d/%m/%Y, %H:%M")
            _msg_ln_1 = f"🎉 @{_winner_name} has vanquished @{_loser_name}! 🎉\n"
            _msg_ln_2 = f"@{_winner_name} bet {round(self.to_eth(int(bet.amount)), 4)}ETH on {_timestamp} that {_token.symbol} would trade "
            _msg_ln_3 = f"{_winner_side} ${round(self.to_eth(int(bet.price)), 4)}, (now ${round(current_price, 4)}) and @{_loser_name} took the other side."
            _msg = _msg_ln_1 + _msg_ln_2 + _msg_ln_3

            # return the result to the client
            return SettleBetResponse(success=True, tx_hash=tx_hash, error_msg=None,
                                     success_msg=_msg, bet=bet)
        else:
            logger.warning(f"settle txn failed! (id:{bet.id})")
            _msg = f"settle txn failed! (id:{bet.id})"
            _reason = ApiV2.get_txn_error(self.RPC_URL, tx_hash)
            if _reason is not None and _reason.__contains__("bet has already been settled or invalidated"):
                # succes=true with an error message is uh... not great
                # but technically the bet was settled... in this case, don't push a message to the client
                return SettleBetResponse(success=True, tx_hash=tx_hash, bet=bet,
                                         error_msg=_reason, over_wins=_over_wins)
            return SettleBetResponse(success=False, tx_hash=tx_hash, bet=bet, error_msg=_msg)

    # memory/db sync happens here, based on result of settle_bet, not in settle_bet itself
    def settle_outstanding(self) -> list[SettleBetResponse]:
        settled = False
        current_block = self.get_l1_block_number()
        if current_block is None:
            logger.error("couldn't get current block number!")
            settled = True
        responses = []
        failures = []
        while not settled:
            # peek at the next bet to settle
            next_expiration = self.bet_cache.peek()

            # if nothing left to settle as of current block, we're done
            if next_expiration is None:
                settled = True
                break
            if next_expiration > current_block:
                settled = True
                break

            # otherwise, pop the bet off the queue, and try to settle it
            _bet_to_settle = self.bet_cache.pop()
            resp = self.settle_bet(_bet_to_settle)

            # if the txn fails for some reason, re-queue the bet (after trying other eligible bets)
            if not resp.success:
                logger.error(f"error settling bet (id:{_bet_to_settle.id})! {resp.error_msg}")
                failures.append(_bet_to_settle)
            else:
                # if the txn succeeds, drop the bet from the database
                self.active_bets_db.delete_one({'id': _bet_to_settle.id})
                if resp.error_msg:
                    logger.warning(f"settle_bet(bet id:{_bet_to_settle.id}) returned {resp.error_msg}")
                logger.debug(f"dropped bet (id: {_bet_to_settle.id}) from active bets db after successful settle")

            responses.append(resp)

        # re-queue the bets which failed to settle
        for fail in failures:
            self.bet_cache.push(fail)
            logger.debug("re-queued bet (id: {_bet_to_settle.id}) after failed settle")

        return responses
        # need to check if error is due to already settled bet, and remove it if so
