import web3.testing

from .apiv2 import ApiV2
from .schema import User, Bet, Token
import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv


# instantiate the API
base_dir = os.path.dirname(os.path.abspath(__file__))
dotenv_path = Path(base_dir).parent / '.env'
print(dotenv_path)
load_dotenv(dotenv_path=dotenv_path)

CONTRACT_ADDR = os.getenv("CONTRACT_ADDR")
# PRIVATE_KEY = os.getenv("PRIVATE_KEY")
# INFURA_KEY = os.getenv("RPC_URL")
# RPC_URL = f"https://goerli.infura.io/v3/{INFURA_KEY}"
RPC_URL = "http://127.0.0.1:8545"


api = ApiV2(contract_addr=CONTRACT_ADDR, rpc_url=RPC_URL)
BLOCK_TIME_ESTIMATE = 1685899790

# create test users
ALICE_PK = "TODO_ADD_API_KEY"
BOB_PK = "TODO_ADD_API_KEY"
w3_alice = api.w3.eth.account.from_key(ALICE_PK)
w3_bob = api.w3.eth.account.from_key(BOB_PK)
alice = User(user_name="Alice", id=1, wallet_addr=w3_alice.address)
bob = User(user_name="Bob", id=2, wallet_addr=w3_bob.address)


def test_create_users():
    print("======== TESTING CREATE USERS =========")
    print(f"create alice: {api.create_unverified_user(alice)}")
    print(f"create alice: {api.create_unverified_user(bob)}")


def test_verify_users():
    print("======== TESTING VERIFY USERS =========")
    alice_signature = "TODO_ADD_API_KEY"
    bob_signature = "TODO_ADD_API_KEY"
    print(api.verify_user_by_id(alice.id, alice_signature))
    print(api.verify_user_by_id(bob.id, bob_signature))


def simulate_user_deposits():
    print("======== SIMULATING USER DEPOSITS =========")
    print(api.test_deposit(w3_alice))
    print(api.test_deposit(w3_bob))


def test_get_balances():
    print("======== TESTING GET USER BALANCES =========")
    alice_avail, alice_locked = api.get_user_balance_by_id(alice.id)
    bob_avail, bob_locked = api.get_user_balance_by_id(bob.id)
    print(f"alice avail: {alice_avail}\t\talice locked: {alice_locked}")
    print(f"bob avail: {bob_avail}\t\tbob locked: {bob_locked}")


def test_verify_raw_signature():
    msg = f"authorize TODO_ADD_TG_HANDLE pvpbet v0"
    wallet_addr = "TODO_ADD_API_KEY"
    signature = "TODO_ADD_API_KEY"

    success, msg = api.validate_signature(msg, wallet_addr, signature)
    print(f"derived_addr={msg}")
    print(f"success={success}")


def test_request_bets():
    print("======== TESTING BET REQUEST =========")
    sui = api.get_tokens_from_expr("SUI")[0]
    imgnai = api.get_tokens_from_expr("IMGNAI")[0]
    bitcoin = api.get_token_by_id(1)

    # chat id, user, over=true/under=false, offer valid till, amount, duration, price, token, counterparty=None
    # 2 is throwaway chat id, 1 is chat id for rest of testing workflow
    print(api.request_bet(1, alice.id, True, "10m", "$10", "15m", 420.69, sui))
    print(api.request_bet(1, bob.id, True, "10m", "$10", "15m", 420.69, imgnai, counterparty="Alice"))
    print(api.request_bet(2, alice.id, True, "1d", "$10.1234", "12h", 0.0001234, sui))
    print(api.request_bet(2, bob.id, True, "1d", "1900$", "12h", 0.0001234, sui))
    print(api.request_bet(2, bob.id, True, "1d", "0.1ETH", "12h", 99999, bitcoin))
    print(api.request_bet(2, bob.id, True, "1d", "0.1", "12h", 0.0001234, bitcoin))

    # alice should have 3 pending
    # bob should have 4 pending


def test_get_bets_by_chat_id(_id: int):
    print("======== TESTING GET BETS BY CHAT ID =========")
    _bets = api.get_bets_by_chat_id(_id)
    print(f"active count: {len(_bets.active)}\npending count: {len(_bets.pending)}\n")


def test_get_bets_by_user_id():
    print("======== TESTING GET BETS BY USER ID =========")
    alice_bets = api.get_bets_by_user_id(1)
    bob_bets = api.get_bets_by_user_id(2)
    print(f"alice:\n\tactive count: {len(alice_bets.active)}\n\tpending count: {len(alice_bets.pending)}")
    print(f"bob:\n\tactive count: {len(bob_bets.active)}\n\tpending count: {len(bob_bets.pending)}")


def test_accept_bets():
    print("======== TESTING ACCEPT BETS =========")
    pending = api.get_bets_by_chat_id(1).pending
    for bet_request in pending:
        if bet_request.counterparty is None:
            print(api.accept_bet(caller_id=bet_request.created_by,
                                 chat_id=bet_request.chat_created_in, bet_id=bet_request.id))
        else:
            print(api.accept_bet(caller_id=bet_request.counterparty,
                                 chat_id=bet_request.chat_created_in, bet_id=bet_request.id))


def log_bet_cache():
    print("======== LOGGING BET CACHE EXPIRATION STATUS =========")
    buf = []
    for _ in range(len(api.bet_cache)):
        buf.append(api.bet_cache.pop())

    _current_block = api.w3.eth.block_number
    print(f"current block: {_current_block}")
    for bet in buf:
        print(f"bet id: {bet.id}, exp: {bet.expiry}")
        api.bet_cache.push(bet)


def anvil_command(cmd: str, args=None):
    if args is None:
        args = []
    url = "http://127.0.0.1:8545"
    headers = {"Content-Type": "application/json"}
    data = {"jsonrpc": "2.0", "method": "anvil_mine", "params": args, "id": 420}
    response = requests.post(url, headers=headers, data=json.dumps(data))

    print(response.text)


def advance_block(num_blocks):
    current_block = api.w3.eth.block_number
    anvil_command("evm_mine", [num_blocks])
    print(f"{current_block} -> {api.w3.eth.block_number}")


def test_settle_bets():
    print("======== TESTING SETTLE BETS =========")
    settled = api.settle_outstanding()
    print(f"settled count: {len(settled)}")
    for resp in settled:
        if resp.success:
            print(f"settled bet id: {resp.bet.id}, msg={resp.success_msg}")
        else:
            print(f"failed to settle bet id {resp.bet.id}, msg={resp.error_msg}")


def test_cmc_API():
    def test_by_expr(expr: str):
        print(f"%%% expr: {expr} %%%")
        _tokens = api.get_tokens_from_expr(expr)
        print(f"get_tokens_from_expr -> {_tokens}")
        _best_token = min(_tokens, key=lambda x: x.rank)
        print(f"best token: {_best_token}")
        print(f"get_token_by_id({_best_token.id}) -> {api.get_token_by_id(_best_token.id)}")
        print(f"get_token_price({_best_token}) -> {api.get_token_price(_best_token)}")

    print(f"======== TESTING CMC API =========")
    test_by_expr("SUI")
    test_by_expr("IMGNAI")
    test_by_expr("bitcoin")


def test_create_duplicate_users():
    test_create_users()
    test_create_users()


# test_verify_raw_signature()
test_create_users()
test_verify_users()
simulate_user_deposits()
test_get_balances()
test_request_bets()
test_get_bets_by_chat_id(1)
test_get_bets_by_user_id()
test_accept_bets()
test_get_bets_by_chat_id(1)
log_bet_cache()
advance_block(55)
test_settle_bets()
log_bet_cache()
test_create_duplicate_users()
# test_cmc_API()
