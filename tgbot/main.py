import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from pydantic import ValidationError
from pathlib import Path
from dotenv import load_dotenv
from .apiv2 import ApiV2
from .schema import User, AcceptBetResponse
import logging
from datetime import datetime

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Set up a FileHandler for output to a file
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.INFO)  # Set the minimum level for this handler
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Add the handlers to the logger
logger.addHandler(file_handler)

logger.info("logger started")

def wei_to_eth(n: int) -> float:
    return n / 1000000000000000000


def eth_to_wei(n: float) -> int:
    return int(n * 1000000000000000000)


def fmt_amount(n: str | int) -> float:
    return round(wei_to_eth(int(n)), 4)


async def in_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != update.effective_user.id:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="We should talk in private...")
        return False
    return True


def get_bet_args() -> str:
    return "/bet <@counterparty> <size> <token name> <over/under> <token price> <time from now>"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _msg = "Commands:\n/setup <wallet address> to link your wallet with your telegram handle\n" \
           "/verify <signature> to verify your wallet\n/balance- show your balance in the bet contract\n" \
           "/wallet to get your wallet info\n/deactivate to deactivate your account\n" \
           "/accept <bet id> to accept a bet that's been offered in the chat\n" \
           "/bets to list all bets in the chat\n" + get_bet_args()
    await update.message.reply_text(_msg)


async def setup(api: ApiV2, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_private(update, context):
        return 0

    if len(context.args) != 1:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="usage: /setup <wallet addr>")
        return 0

    wallet_addr = context.args[0]

    if type(wallet_addr) != str or len(wallet_addr) != 42:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="invalid wallet addr")
        return 0

    # put user info in the backend db
    user_name = update.effective_user.username
    user_id = update.effective_user.id
    new_user = User(id=user_id, user_name=user_name, wallet_addr=wallet_addr)
    _success, _msg = api.create_unverified_user(new_user)

    await context.bot.send_message(chat_id=update.effective_chat.id, text=_msg)
    return 0


async def deactivate(api: ApiV2, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_private(update, context):
        return 0
    _res = api.deactivate_user_by_id(update.effective_user.id)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=_res)
    return 0


async def verify(api: ApiV2, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_private(update, context):
        return 0
    _user = api.get_user_by_id(update.effective_user.id)
    if _user is None:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="You haven't setup your wallet yet! try: /setup <wallet addr>")
        return 0

    if _user.verified:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="You're already verified!")
        return 0

    if len(context.args) != 1:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="incorrect usage\n try: /verify <signature>")
        return 0

    _success, _msg = api.verify_user_by_id(update.effective_user.id, context.args[0])
    if _success:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=_msg)
        return 0
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=_msg)
        return 0


async def balance(api: ApiV2, update: Update, context: ContextTypes.DEFAULT_TYPE):
    _user_id = update.effective_user.id
    _user = api.get_user_by_id(_user_id)

    if _user is None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You need to set up a wallet first!")
        return 0

    _avail, _locked = api.get_user_balance_by_id(_user_id)
    if _avail is not None and _locked is not None:
        _avail_eth = wei_to_eth(_avail)
        _locked_eth = wei_to_eth(_locked)
        _avail_usd = _avail_eth * api.eth_price
        _locked_usd = _locked_eth * api.eth_price
        _msg = f"{_user.user_name} PvPbet balances:\n Available: ${round(_avail_usd,2)} ({_avail_eth}ETH)\n" \
               f" Locked: ${round(_locked_usd, 2)} ({wei_to_eth(_locked)}ETH)"
    else:
        _msg = f"No wallet found!"

    await context.bot.send_message(chat_id=update.effective_chat.id, text=_msg)


async def wallet(api: ApiV2, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_private(update, context):
        return 0

    _user_id = update.effective_user.id
    _user = api.get_user_by_id(_user_id)

    if _user is None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You need to set up a wallet first!")
        return 0

    _msg = f"{_user.user_name} PvPbet wallet:\n {_user.wallet_addr}"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=_msg)


# /bet @Bob $10 $SUI over $1.15 12h
async def bet(api: ApiV2, update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) != 6:
        _msg = "incorrect arguments... example usage:\n" \
               "/bet @alice $10 BTC over $25000 10d\n" \
               "betting $10 BTC that BTC will be over $25000 in 10 days\n" \
               "note: you can use '@any'/'any'/'@all'/'all'/'*' to offer the bet against anyone\n"\
               "bet size format is $420.69 for USD denomination, defaults to ETH if no \"$\" included\n"\
               "if it's a really obscure shitcoin, make sure to specify by the name in the coinmarketcap url\n"\
               "expiration date formats are: 1d, 1w, 1mo\n"
        await context.bot.send_message(chat_id=chat_id, text=get_bet_args())
        return 0

    user_id = update.effective_user.id
    counterparty = context.args[0][1:]
    if counterparty in ["any", "all", "*", "@any", "@all"]:
        counterparty = None

    _value_expr = context.args[1]
    _token = context.args[2]
    _side = context.args[3]
    if context.args[4][0] == "$":
        _price = float(context.args[4][1:])
    else:
        _price = float(context.args[4])
    _time_expr = context.args[5]

    # this check is done client-side because in future the token selection flow will be more complex
    # and probably require a back-and forth in the telegram client
    if type(_token) is int:
        _token = api.get_token_by_id(_token)
        if _token is None:
            await context.bot.send_message(chat_id=chat_id, text="invalid token id")
            return 0
    elif type(_token) is str:
        _token_candidates = api.get_tokens_from_expr(_token)
        if _token_candidates is None:
            await context.bot.send_message(chat_id=chat_id, text="invalid token name")
            return 0
        # if there's only one token that matches the expr, use it
        if len(_token_candidates) == 1:
            _token = _token_candidates[0]
        else:
            _best_token = min(_token_candidates, key=lambda x: x.rank)
            # if one token is clearly better than the rest, use it
            if _best_token.rank < 750:
                _token = _best_token
            # otherwise, every token is some random ambiguous shitcoin, so require a name
            else:
                _msg = "ambiguous token name, please specify by name (use the final part of the coinmarketcap url)"
                await context.bot.send_message(chat_id=chat_id, text=_msg)
                return 0

    _over = _side == "over"
    # hardcoding bet offer expiration at 5 mins for now to simplify user flow
    _valid_till = "5m"
    try:
        _bet_req = api.request_bet(chat_id, user_id, _over, _valid_till, _value_expr,
                                   _time_expr, _price, _token, counterparty=counterparty)
    except ValidationError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="invalid bet parameters!")
        return 0

    if _bet_req.success:
        _txt = f"⚔️challenge sent! ID: {_bet_req.bet_proposal.id} ⚔️"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=_txt)
        return 0

    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=_bet_req.error_msg)
        return 0


async def accept(api: ApiV2, update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if len(context.args) != 1:
        await context.bot.send_message(chat_id=chat_id, text=" ❌incorrect command usage, need bet ID to accept")
        return 0

    bet_req_id: int = int(context.args[0])
    # TODO: do this async
    context.bot.send_message(chat_id=chat_id, text="processing bet accept request...")


    # CHECKS ARE DONE IN THE API LAYER, so we just yeet that bitch immediately:
    response: AcceptBetResponse = api.accept_bet(caller_id=update.effective_user.id,
                                                 chat_id=chat_id, bet_id=bet_req_id)
    if response.success:
        await context.bot.send_message(chat_id=chat_id, text=f"💸 Bet successfully created!💸\n txn hash: {response.tx_hash}")
        return 0
    else:
        await context.bot.send_message(chat_id=chat_id, text=response.error_msg)
        return 0


# TODO: gets bets. usage: /bets [offered|accepted|all] [user]
# current: /bets, no args, gets all bets in chat or by user
async def bets(api: ApiV2, update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _user = api.get_user_by_id(update.effective_user.id)

    # if user calling this fn from private chat, return the user's bets
    if _user is None:
        await context.bot.send_message(chat_id=chat_id, text="we couldn't find your account, "
                                                             "make sure to run /setup first")
        return 0

    if chat_id == _user.id:
        _bets = api.get_bets_by_user_id(_user.id)
    else:
        _bets = api.get_bets_by_chat_id(chat_id)

    if _bets is not None and len(_bets.active) + len(_bets.pending) > 0:
        # _text = f"pending: {_bets.pending}\n active: {_bets.active}"
        _text = "🔎 currently offered:" if len(_bets.pending) > 0 else ""
        for bet_struct in _bets.pending:
            _open_to = bet_struct.counterparty if bet_struct.counterparty else "Anyone ‼️"
            _side = "under" if bet_struct.creator_over else "over"
            _offer_valid_for = str(bet_struct.valid_till - datetime.now())
            d1 = f"\nID: {bet_struct.id}\n Open to: {api.get_user_by_id(_open_to).user_name} for {_offer_valid_for}\n"
            d2 = f"${bet_struct.token.symbol} {_side} ${fmt_amount(bet_struct.price)} in {bet_struct.str_exp}\n"
            d3 = f"Amount wagered: {fmt_amount(bet_struct.amount)}\n"
            _text += d1 + d2 + d3

        _text += "⏳ currently active:" if len(_bets.active) > 0 else ""
        for bet_struct in _bets.active:
            _over_user = api.get_user_by_id(bet_struct.over_user_id)
            _under_user = api.get_user_by_id(bet_struct.under_user_id)
            _symbol = api.get_token_by_id(int(bet_struct.token)).symbol  # TODO: hack, fix this
            _blocks_left = bet_struct.expiry - api.get_l1_block_number()
            _time_est = (_blocks_left) * 12
            _mins = int(_time_est / 60)
            _hrs = _mins / 60
            _wks = (_hrs / 24) / 7
            _time_str = f"~{_wks} weeks" if _wks > 1 else f"~{_hrs} hours" if _hrs > 1 else f"~{_mins} minutes"

            d1 = f"\nID: {bet_struct.id}\n Over: @{_over_user.user_name}\n Under: @{_under_user.user_name}\n"
            d2 = f"${_symbol} trades at ${wei_to_eth(int(bet_struct.price))}"
            d3 = f" in {_time_str} ({_blocks_left} blocks)\n"
            d4 = f"Amount wagered: {fmt_amount(bet_struct.amount)} ETH\n"
            _text += d1 + d2 + d3 + d4
        await context.bot.send_message(chat_id=chat_id, text=_text)
        return 0
    else:
        await context.bot.send_message(chat_id=chat_id, text="no bets found!")
        return 0


# if user calling this fn from private chat, return the user's bets
async def settle_bets(api: ApiV2, context: ContextTypes.DEFAULT_TYPE):
    logger.info("settle bets callback running...")
    settled_bets = api.settle_outstanding()
    logger.info(f"settled {len(settled_bets)} bets")
    for bet_response in settled_bets:
        if bet_response.success_msg:
            await context.bot.send_message(chat_id=bet_response.bet.chat_created_in,
                                           text=bet_response.success_msg)
        if bet_response.error_msg:
            print(f"ERROR: {bet_response.error_msg}")

if __name__ == '__main__':
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = Path(base_dir).parent / '.env'
    print(dotenv_path)
    load_dotenv(dotenv_path=dotenv_path)

    # apiv2
    CONTRACT_ADDR = os.getenv("CONTRACT_ADDR")
    PK = os.getenv("PRIVATE_KEY")
    RPC_URL = os.getenv("RPC_URL")
    L1_RPC_URL = os.getenv("L1_RPC_URL")
    backend_api = ApiV2(contract_addr=CONTRACT_ADDR, rpc_url=RPC_URL, pk=PK, l1_rpc_url=L1_RPC_URL)
    del CONTRACT_ADDR, PK, RPC_URL

    # tg tgbot setup
    API_KEY = os.getenv("TG_TOKEN")
    application = ApplicationBuilder().token(API_KEY).concurrent_updates(True).build()


    async def settle_bets_callback(context: ContextTypes.DEFAULT_TYPE):
        await settle_bets(backend_api, context=context)

    async def wallet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await wallet(backend_api, update=update, context=context)

    async def setup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await setup(backend_api, update=update, context=context)

    async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await verify(backend_api, update=update, context=context)

    async def deactivate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await deactivate(backend_api, update=update, context=context)

    async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await balance(backend_api, update=update, context=context)

    async def bet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await bet(backend_api, update=update, context=context)

    async def bets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await bets(backend_api, update=update, context=context)

    async def accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await accept(backend_api, update=update, context=context)

    job_queue = application.job_queue
    job_queue.run_repeating(settle_bets_callback, interval=300)

    start_handler = CommandHandler('start', start)
    bet_handler = CommandHandler('bet', bet_callback)
    bets_handler = CommandHandler('bets', bets_callback)
    balance_handler = CommandHandler('balance', balance_callback)
    setup_handler = CommandHandler('setup', setup_callback)
    verify_handler = CommandHandler('verify', verify_callback)
    deactivate_handler = CommandHandler('deactivate', deactivate_callback)
    accept_handler = CommandHandler('accept', accept_callback)
    wallet_handler = CommandHandler('wallet', wallet_callback)
    application.add_handler(wallet_handler)
    application.add_handler(deactivate_handler)
    application.add_handler(start_handler)
    application.add_handler(bet_handler)
    application.add_handler(bets_handler)
    application.add_handler(balance_handler)
    application.add_handler(verify_handler)
    application.add_handler(setup_handler)
    application.add_handler(accept_handler)

    # test_handler = CommandHandler('test', test)
    # application.add_handler(test_handler)

    application.run_polling()
