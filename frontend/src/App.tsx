import './App.css'
import { useState, useEffect } from 'react'
import detectEthereumProvider from '@metamask/detect-provider'

const App = () => {
  const [_, setHasProvider] = useState<boolean | null>(null)
  const initialState = { accounts: [], balance: "", chainId: "" }
  const [wallet, setWallet] = useState(initialState)

  const [isConnecting, setIsConnecting] = useState(false)  /* New */
  const [error, setError] = useState(false)                /* New */
  const [errorMessage, setErrorMessage] = useState("")     /* New */
  const [msg, setMsg] = useState("");
  const [hasRefLink, setRefLink] = useState(false);
  const [signedMessage, setSignedMessage] = useState('');

  const formatMessage = (user_name: string) => {
    if (user_name.charAt(0) == '?') {
      user_name = user_name.substring(1);
    }
    return "authorize " + user_name + " pvpbet v0";
  }

  useEffect(() => {
    // Get the search parameters from the URL
    const params = window.location.search;

    // If the 'msg' parameter is present, set the 'msg' state to its value
    if (params) {
      setRefLink(true);
      setMsg(params);
    }
  }, []);


  useEffect(() => {
    const refreshAccounts = (accounts: any) => {
      if (accounts.length > 0) {
        updateWallet(accounts)
      } else {
        // if length 0, user is disconnected
        setWallet(initialState)
      }
    }

    const refreshChain = (chainId: any) => {
      setWallet((wallet) => ({ ...wallet, chainId }))
    }

    const getProvider = async () => {
      const provider = await detectEthereumProvider({ silent: true })
      setHasProvider(Boolean(provider))

      if (provider) {
        const accounts = await window.ethereum.request(
          { method: 'eth_accounts' }
        )
        refreshAccounts(accounts)
        window.ethereum.on('accountsChanged', refreshAccounts)
        window.ethereum.on("chainChanged", refreshChain)
      }
    }

    getProvider()

    return () => {
      window.ethereum?.removeListener('accountsChanged', refreshAccounts)
      window.ethereum?.removeListener("chainChanged", refreshChain)
    }
  }, [])

  const updateWallet = async (accounts: any) => {
    const balance = await window.ethereum!.request({
      method: "eth_getBalance",
      params: [accounts[0], "latest"],
    })
    const chainId = await window.ethereum!.request({
      method: "eth_chainId",
    })
    setWallet({ accounts, balance, chainId })
  }

  const handleConnect = async () => {                   /* Updated */
    setIsConnecting(true)                               /* New */
    await window.ethereum.request({                     /* Updated */
      method: "eth_requestAccounts",
    })
      .then((accounts: []) => {                            /* New */
        setError(false)                                   /* New */
        updateWallet(accounts)                            /* New */
      })                                                  /* New */
      .catch((err: any) => {                               /* New */
        setError(true)                                    /* New */
        setErrorMessage(err.message)                      /* New */
      })                                                  /* New */
    setIsConnecting(false)                              /* New */
  }


  const handleSign = async () => {
    try {
      if (msg) {
        const signature = await window.ethereum.request({
          method: 'personal_sign',
          params: [window.ethereum.selectedAddress, formatMessage(msg)],
        });
        setSignedMessage(signature);
      }
    } catch (error) {
      console.error("There was an issue signing the message", error);
    }
  };

  const disableConnect = Boolean(wallet) && isConnecting

  return (
    <div className="App">
      <h1> ⚔️ Welcome to PvPbet! ⚔️</h1>
      <div>
        <a href="https://arbiscan.io/address/TODO_ADD_CONTRACT_ADDR" target="blank"> Deposit/Withdraw </a>
      </div>
      <h3>PvPbet is a telegram bot that lets you place 1v1 bets on crypto price movements in all your degen tg chats.</h3>
      <p> !PLEASE NOTE THIS IS IN ALPHA RELEASE AND COMPLETELY UNAUDITED, USE EXTREMELY SMALL FUNDS! </p>
      <div className="Interface">
        {/* <div>Injected Provider {hasProvider ? 'DOES' : 'DOES NOT'} Exist</div> */}

        {window.ethereum?.isMetaMask && wallet.accounts.length < 1 &&
          /* Updated */
          <button disabled={disableConnect} onClick={handleConnect}>Connect MetaMask</button>
        }

        {wallet.accounts.length > 0 &&
          <>
            <div>Wallet Accounts: {wallet.accounts[0]}</div>
            <div>Wallet Balance: {Number(wallet.balance) * 1e-18}</div>
            <div>ChainId: {Number(wallet.chainId) === 42161 ? 'Arbitrum Mainnet' : Number(wallet.chainId)}</div>

            {!hasRefLink &&
              <div>
                <label>Enter your telegram username: </label>
                <input type="text" onChange={e => setMsg(e.target.value)} />
              </div>
            }
            <div className='buttons-dashboard'>
              <button onClick={handleSign}>Verify Wallet Ownership</button>
            </div>
            {signedMessage && <div> <br></br>/verify {signedMessage} <p>^ copy/paste this in your chat with the bot to verify your wallet</p></div>}
          </>
        }
        {error && (                                        /* New code block */
          <div onClick={() => setError(false)}>
            <strong>Error:</strong> {errorMessage}
          </div>
        )
        }
      </div>
    </div>
  )
}

export default App
