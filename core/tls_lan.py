"""Certificat TLS du listener LAN (satellites et agent Windows).

Le serveur genere une fois pour toutes une cle et un certificat auto-signe dans
certs/ (jamais versionne). Les clients ne font pas confiance a une autorite
publique : ils « epinglent » ce certificat precis (copie du .crt chez eux). Un
appareil du Wi-Fi ne peut donc ni lire les echanges (jetons, audio, reponses),
ni se faire passer pour le serveur. La cle privee ne quitte jamais le serveur.
"""
import datetime as dt
import ipaddress
import logging
import socket
import ssl
from pathlib import Path

LOG = logging.getLogger("jarvis")

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / "certs"
CERTIFICAT = DOSSIER / "jarvis-lan.crt"
CLE = DOSSIER / "jarvis-lan.key"


def _adresses_locales():
    """IP de la machine (pour le champ SAN ; les clients ne verifient pas le nom)."""
    adresses = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            adresses.add(info[4][0])
    except OSError:
        pass
    return sorted(adresses)


def assurer_certificat():
    """Renvoie (certificat, cle), en les creant au premier appel."""
    if CERTIFICAT.exists() and CLE.exists():
        return CERTIFICAT, CLE
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    DOSSIER.mkdir(exist_ok=True)
    cle = ec.generate_private_key(ec.SECP256R1())
    nom = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis LAN")])
    maintenant = dt.datetime.now(dt.timezone.utc)
    noms = [x509.DNSName(socket.gethostname() or "jarvis"), x509.DNSName("localhost")]
    noms += [x509.IPAddress(ipaddress.ip_address(a)) for a in _adresses_locales()]
    certificat = (
        x509.CertificateBuilder()
        .subject_name(nom).issuer_name(nom)
        .public_key(cle.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(maintenant - dt.timedelta(minutes=5))
        .not_valid_after(maintenant + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=False,
            content_commitment=False, key_encipherment=False, data_encipherment=False,
            key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(noms), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(cle.public_key()), critical=False)
        .sign(cle, hashes.SHA256())
    )
    CLE.write_bytes(cle.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    CERTIFICAT.write_bytes(certificat.public_bytes(serialization.Encoding.PEM))
    LOG.info("tls_lan: certificat LAN cree (%s)", CERTIFICAT.name)
    return CERTIFICAT, CLE


def classe_ssl():
    """Classe ssl.SSLContext d'origine, meme apres truststore.inject_into_ssl().

    Jarvis injecte truststore (magasin de certificats Windows pour les API web), ce
    qui remplace ssl.SSLContext dans tout le processus. Cote serveur, la classe de
    truststore exige un certificat du client et ferme toutes les connexions ; cote
    client, elle melerait le magasin systeme a l'epinglage. Le TLS du LAN garde donc
    la classe de la bibliotheque standard (celle dont truststore herite)."""
    return next(c for c in ssl.SSLContext.__mro__ if c.__module__ == "ssl")


def contexte_serveur(certificat, cle):
    """Contexte TLS du listener LAN (TLS 1.2 minimum, aucun certificat client).

    Les reglages par defaut de PROTOCOL_TLS_SERVER suffisent : on ne passe pas par
    les setters Python de ssl (minimum_version, verify_mode...), qui bouclent sans
    fin sur la classe d'origine quand truststore est injecte. On verifie seulement."""
    contexte = classe_ssl()(ssl.PROTOCOL_TLS_SERVER)
    if contexte.minimum_version < ssl.TLSVersion.TLSv1_2:
        raise ssl.SSLError("TLS 1.2 minimum indisponible")
    if contexte.verify_mode != ssl.CERT_NONE:      # les postes n'ont pas de certificat
        raise ssl.SSLError("le listener LAN ne doit pas exiger de certificat client")
    contexte.load_cert_chain(str(certificat), str(cle))
    return contexte


def empreinte():
    """Empreinte SHA-256 du certificat, pour comparer a l'oeil cote client."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = x509.load_pem_x509_certificate(CERTIFICAT.read_bytes())
    return cert.fingerprint(hashes.SHA256()).hex(":").upper()
