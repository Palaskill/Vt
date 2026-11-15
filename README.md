# VetStock

Application locale pour la gestion des médicaments d'un cabinet vétérinaire : stock par lot, dates de péremption, seuils et alertes, achats, fournisseurs et ventes. L'interface est disponible en français et en arabe.

## Fonctionnement

- Un ordinateur Windows héberge l'application et les données du cabinet.
- Les autres appareils accèdent à l'application par le Wi-Fi local du cabinet après configuration HTTPS.
- Le stock et les ventes fonctionnent sans accès Internet une fois l'installation initiale terminée.
- Les droits distinguent l'administrateur des employés. Les seuils et les destinataires des alertes sont configurables.
- Les boîtes fermées et les comprimés vendus à l'unité sont suivis séparément ; les lots non périmés qui expirent le plus tôt sont proposés en priorité.
- Les achats, les livraisons partielles, les paiements et les pièces jointes des factures fournisseurs sont conservés localement.

## Installation sur Windows

1. Télécharger le dépôt puis extraire les fichiers sur le PC du cabinet.
2. Installer Python 3.10 ou une version plus récente.
3. Suivre les instructions du fichier [LISEZ_MOI.txt](LISEZ_MOI.txt), notamment pour configurer HTTPS et les certificats sur les appareils du cabinet.
4. Lancer `Demarrer_VetStock.bat` et créer le compte administrateur.

La configuration HTTPS initiale télécharge une dépendance et nécessite Internet. Ensuite, les fonctions locales peuvent être utilisées hors ligne. Le PC doit rester allumé pendant l'utilisation.

## État et limites

Cette première version a été vérifiée dans un environnement Linux. L'installation sur Windows et le fonctionnement avec les téléphones et lecteurs du cabinet restent à valider avec des données fictives avant utilisation réelle.

- La TVA n'est pas calculée ; les documents de vente sont des **brouillons** à faire vérifier avant utilisation comme factures fiscales.
- Les notifications sur téléphone dépendent du navigateur et fonctionnent lorsque l'application reste ouverte et active. Les SMS et WhatsApp ne sont pas inclus.
- Les données et sauvegardes sont enregistrées localement sans chiffrement par l'application ; l'accès au PC et aux supports doit être protégé.
- La caméra et les codes-barres EAN-13/EAN-8 nécessitent un appareil compatible et une connexion HTTPS approuvée.

Le dossier `data/`, les bases SQLite, les sauvegardes et les clés locales sont exclus de Git. Ne les ajoutez pas au dépôt.

## Vérifications

```bash
python -m unittest discover -s tests -v
node tests/test_barcode.js
```

Le deuxième contrôle nécessite Node.js ; l'application elle-même fonctionne avec Python.
