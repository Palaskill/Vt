#!/usr/bin/env python3
"""Create a private CA and a server certificate for this cabinet's LAN.

Requires the optional, locally installed `cryptography` package. No keys are
uploaded, and the program does not install trust on any device automatically.
"""
from __future__ import annotations
import argparse
import datetime as dt
import ipaddress
import os
import secrets
import socket
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
except ImportError:
    raise SystemExit('Module cryptography absent. Executez : py -3 -m pip install --user cryptography')

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('VETOSTOCK_DATA', ROOT / 'data')).resolve()
CERTS = DATA / 'certs'


def private_write(path, content):
    path.write_bytes(content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ips():
    values = {'127.0.0.1'}
    try:
        values.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1', 1))  # Select a route only; no packet is sent.
            values.add(s.getsockname()[0])
    except OSError:
        pass
    return values


def main():
    parser = argparse.ArgumentParser(description='Configuration HTTPS locale VetStock')
    parser.add_argument('--ip', action='append', default=[], help='Adresse IPv4 locale supplémentaire')
    args = parser.parse_args()
    addresses = ips()
    for value in args.ip:
        addresses.add(str(ipaddress.IPv4Address(value)))
    CERTS.mkdir(parents=True, exist_ok=True)
    ca_key_file, ca_file = CERTS/'ca-key.pem', CERTS/'ca.pem'
    now = dt.datetime.now(dt.timezone.utc)
    if ca_key_file.exists() != ca_file.exists():
        raise SystemExit('Autorité incomplète : contactez une personne technique. Ne supprimez pas les clés sans sauvegarde.')
    if ca_file.exists():
        ca_key = serialization.load_pem_private_key(ca_key_file.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_file.read_bytes())
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'VetStock cabinet'),
                          x509.NameAttribute(NameOID.COMMON_NAME, 'VetStock CA '+secrets.token_hex(4))])
        ca_cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=5))
            .not_valid_after(now+dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,
                data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,
                encipher_only=False,decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
        private_write(ca_key_file, ca_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ca_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    (CERTS/'CA-VetStock.cer').write_bytes(ca_cert.public_bytes(serialization.Encoding.DER))
    server_key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME,'VetStock cabinet'),
                      x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    sans = [x509.DNSName('localhost')]
    hostname = socket.gethostname().lower()
    if hostname and len(hostname) <= 253:
        try:
            sans.append(x509.DNSName(hostname))
        except ValueError:
            pass
    for value in sorted(addresses):
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(value)))
        except ValueError:
            pass
    server_cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_cert.subject)
        .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName(sans),critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False)
        .sign(ca_key,hashes.SHA256()))
    private_write(CERTS/'server-key.pem',server_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    (CERTS/'server.pem').write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    print('\nCertificat HTTPS local créé. Aucun certificat n’a été installé automatiquement.')
    print('Certificat public à installer uniquement sur vos appareils :', CERTS/'CA-VetStock.cer')
    print('Empreinte SHA-256 :', ':'.join(f'{x:02X}' for x in ca_cert.fingerprint(hashes.SHA256())))
    print('Adresses couvertes :', ', '.join(sorted(addresses)))
    print('Conservez le dossier data/certs et ses clés privées sur le PC du cabinet uniquement.')
    print('Redémarrez VetStock après cette configuration. Consultez le guide.')


if __name__ == '__main__':
    main()
