## pvpbet v1


This is an old hacked-together-in-a-week project that lets you bet your friends on crypto prices.

Disclaimer: this is just for fun. If any 3 letter agencies are reading this, i'm not running this anywhere, i'm not taking real rake, etc. This was a fun side project to practice throwing together a marginally nontrivial fullstack app.

It works like this:

1. you deploy the on chain contract, make a telegram bot key, and a coinmarketcap price api key, and plug it into .env files
2. you run the telegram bot and the frontend on some server. the bot is light enough that a free tier ec2 will do.
The frontend is (iirc) more or less one-click deployable on vercel.
3. You and your friends set up and fund arbitrum wallets with some usdc, fund your betting account, etc.
4. you invite the bot to a group chat, and do e.g. `/bet @alice $10 BTC over $70000 10d\` to bet the telegram user alice $10 USDC that btc/usdc is trading over 70k in 10 days time.
5. your bet is automatically placed on-chain.
6. when a bet is about to expire, the tg bot program will pull the relevant price and settle the bet. If Alice wins, her balance will be credited with the winnings minus a hardcoded rake fee.

Obviously whoever runs this can just arbitrarily settle bets; if Alice runs the program, Bob and Charlie can rest assured that Alice can't steal their money. But if Alice really dislikes Charlie for some reason, she can arbitrarily settle the bet in his favor, whether he actually won the bet or not.

Not perfect, but maybe a reasonable security level for small bets between friends, because as long as the person running the bot isn't malicious, other participants won't be able to renege on their bet once created. 


## Setup

Disclaimer: I wrote this a long time ago, instructions are probably out of date. You'll have to do some manual config and hunt down missing hardcoded keys. I'm under no illusion that anyone will really look at this; as a result i'm too lazy to make a nice install process. That said, if you want to run this for a group of friends, you (realistically mostly gpt4) can probably get it running in day with some minor head scratching.

This was built naively on python telegram bot, and is performant enough for a reasonably large group chat of friends, but definitely not intented to scale beyond that.

You'll want to grep for `TODO_ADD_` to find placeholder api keys in env files and configs and so forth.


#### Backend:
run mongo with

`docker run -d -p 27017:27017 --name mongodb mongo`

#### bookie:
the actual on-chain contracts. Uses foundry framework.

run localnet: `anvil`

build contracts: `forge build`

deploy contract to testnet: `forge script script/Bookie.s.sol:BookieDeploy --rpc-url $GOERLI_RPC_URL --broadcast --verify -vvvv`

deploy contract to arbitrum: `forge script script/Bookie.s.sol:BookieDeploy --fork-url $ARBITRUM_RPC_URL --broadcast --verify -vvvv`

arbitrum deployment hash: `REDACTED`

arbitrum contract addr: `REDACTED`

local account 1 private key: `REDACTED`

local account 1 pubkey: `REDACTED`

localnet contract address: `REDACTED`

goereli contract address `REDACTED`

goereli deployment hash `REDACTED`
```shell
Test result: ok. 14 passed; 0 failed; finished in 7.40ms
| src/Bookie.sol:Bookie contract |                 |        |        |        |         |
|--------------------------------|-----------------|--------|--------|--------|---------|
| Deployment Cost                | Deployment Size |        |        |        |         |
| 1379025                        | 6534            |        |        |        |         |
| Function Name                  | min             | avg    | median | max    | # calls |
| BLOCK_SAFETY_MARGIN            | 261             | 261    | 261    | 261    | 14      |
| INVALIDATION_WINDOW            | 217             | 217    | 217    | 217    | 3       |
| bookieInvalidateBet            | 19450           | 19450  | 19450  | 19450  | 1       |
| deposit                        | 4692            | 24489  | 25307  | 26307  | 24      |
| getLockedBalance               | 581             | 581    | 581    | 581    | 10      |
| getSpendableBalance            | 582             | 672    | 582    | 2582   | 22      |
| invalidateStaleBet             | 653             | 12367  | 758    | 35692  | 3       |
| makeBet                        | 2966            | 161230 | 192718 | 192718 | 13      |
| settleBet                      | 649             | 22881  | 39422  | 39428  | 7       |
| withdraw                       | 2640            | 2640   | 2640   | 2640   | 1       |
```

#### tgbot:
constantly-polling python script that actually keeps the telegram bot alive and listens to user requests
