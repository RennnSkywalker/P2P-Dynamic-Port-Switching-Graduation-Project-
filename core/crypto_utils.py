import os
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes

class CryptoManager:
    def __init__(self, key_size=2048):
        self.key_size = key_size
        self.private_key = None
        self.public_key = None

    def generate_keys(self):
        """Yeni bir RSA anahtar çifti oluşturur."""
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=self.key_size,
        )
        self.public_key = self.private_key.public_key()
        return self.private_key, self.public_key

    def save_keys(self, priv_path, pub_path):
        """Anahtarları PEM formatında dosyalara kaydeder."""
        if not self.private_key or not self.public_key:
            raise ValueError("Keys not generated yet.")
        
        os.makedirs(os.path.dirname(priv_path) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(pub_path) or ".", exist_ok=True)

        with open(priv_path, "wb") as f:
            f.write(self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))
            
        with open(pub_path, "wb") as f:
            f.write(self.public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ))

    def load_private_key(self, path):
        with open(path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(
                f.read(),
                password=None
            )

    def load_public_key(self, path):
        with open(path, "rb") as f:
            return serialization.load_pem_public_key(f.read())

    def encrypt_data(self, data: bytes, target_public_key) -> bytes:
        """Hedefin açık anahtarını kullanarak veriyi şifreler."""
        ciphertext = target_public_key.encrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return ciphertext

    def decrypt_data(self, ciphertext: bytes) -> bytes:
        """Kendi gizli anahtarımızı kullanarak verinin şifresini çözer."""
        if not self.private_key:
            raise ValueError("Private key not loaded.")
        plaintext = self.private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return plaintext
