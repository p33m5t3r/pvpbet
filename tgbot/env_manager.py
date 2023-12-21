import rsa
import os
import base64


# maybe in the future this will be useful but mostly turned out to be a stupid over-engineered idea
class SecretLoader:
    def __init__(self, env_path: str):
        self.env_path = env_path

    @staticmethod
    def file_exists(filename):
        return os.path.isfile(filename)

    def load_env(self) -> dict:
        env = {}
        with open(self.env_path) as f:
            for line in f:
                if line.strip() == '' or line.startswith('#'):
                    # Skip empty lines and comments
                    continue
                key, value = line.strip().split('=', 1)
                env[key] = value
        return env

    @staticmethod
    def load_rsa_keys_from_input() -> (rsa.PublicKey, rsa.PrivateKey):
        paste = input("generating pubkey, privkey from n, e, d, p, q (paste comma-separated string below):")
        s = [int(e.strip(' ')) for e in paste.split(',')]
        # n = int(input("n:\n"))
        # e = int(input("e:\n"))
        # d = int(input("d:\n"))
        # p = int(input("p:\n"))
        # q = int(input("q:\n"))
        try:
            return rsa.PublicKey(s[0], s[1]), rsa.PrivateKey(s[0], s[1], s[2], s[3], s[4])
        except (TypeError, IndexError):
            print("invalid key components, try again.")
            raise

    @staticmethod
    def load_rsa_pubkey() -> rsa.PublicKey:
        n = int(input("generating pubkey... paste first value (n) below:\n"))
        # e = int(input("e:\n"))
        e = 65537
        try:
            _pbk = rsa.PublicKey(n, e)
            print("new pubkey created!")
            print(_pbk)
            return _pbk
        except TypeError:
            print("invalid key components, try again.")
            raise

    @staticmethod
    def encrypt_plaintext_b64(msg: str, pubkey: rsa.PublicKey) -> str:
        return base64.b64encode(rsa.encrypt(msg.encode(), pubkey)).decode()

    @staticmethod
    def decrypt_plaintext_b64(msg: str, privkey: rsa.PrivateKey) -> str:
        return rsa.decrypt(base64.b64decode(msg), privkey).decode()

    def edit_secret_dotenv(self):
        pubkey = privkey = None
        if self.file_exists(self.env_path):
            print("env file already exists. loading (encrypted) contents:")
            env = self.load_env()
            print(env)
            _view = input("input secret to view decrypted values? (y/n)")
            if _view == "y":
                pubkey, privkey = self.load_rsa_keys_from_input()
                for k, v in env.items():
                    decrypted_value = self.decrypt_plaintext_b64(v, privkey)
                    print(f"{k}={decrypted_value}")

        choice = input("write/overwrite new .env.prod file? (y/n)")
        if choice == "y":
            if pubkey is None:
                pubkey = self.load_rsa_pubkey()

            tg_token = input("input tg token:\n")
            bookie_pk = input("input bookie private key:\n")
            infura_key = input("input infura API key:\n")

            tg_str = "TG_TOKEN=" + self.encrypt_plaintext_b64(tg_token, pubkey) + "\n"
            bookie_pk_str = "BOOKIE_PK=" + self.encrypt_plaintext_b64(bookie_pk, pubkey) + "\n"
            infura_key_str = "INFURA_KEY=" + self.encrypt_plaintext_b64(infura_key, pubkey) + "\n"
            file_contents = tg_str + bookie_pk_str + infura_key_str

            with open(self.env_path, 'w') as f:
                f.write(file_contents)

            print("done! new contents:")
            with open(self.env_path, 'r') as f:
                new_contents = f.read()
            print(new_contents)

    def load_secret_dotenv(self) -> dict:
        pubkey, privkey = self.load_rsa_keys_from_input()
        env = self.load_env()

        for k, v in env.items():
            env.update({k: self.decrypt_plaintext_b64(v, privkey)})

        return env

    @staticmethod
    def generate_new_key():
        pubkey, privkey = rsa.newkeys(512)
        print("privkey:")
        print(privkey)


class EnvManager:
    def __init__(self):
        path = input("Enter path to .env.prod (blank for cwd) ")
        if len(path) < 2:
            path = ".env.prod"

        sl = SecretLoader(path)
        debugging_env = {}
        while 1:
            option = int(input("(1) edit/create .env.prod\n(2) load values from .env.prod\n(3) new keys\n(4)view env\n"))
            if option == 1:
                sl.edit_secret_dotenv()
            elif option == 2:
                debugging_env = sl.load_secret_dotenv()
            elif option == 3:
                sl.generate_new_key()
            elif option == 4:
                print(debugging_env)
