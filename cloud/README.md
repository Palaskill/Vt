# VetStock en ligne — préparation

Cette version expérimentale est séparée de la version Windows. Elle utilise un serveur Python sur Render et une base PostgreSQL Supabase ; elle nécessite Internet. Le dépôt GitHub et l'adresse Render sont publics, mais les données et les fonctions sont accessibles uniquement après connexion. Ne jamais placer de données du cabinet ou de secrets dans ce dépôt public.

La version en ligne ne partage pas automatiquement les données avec la version locale. Elle ne propose pas encore de synchronisation hors ligne, de SMS ou WhatsApp, ni de sauvegarde automatique configurée. Les notifications dépendent du navigateur lorsque l'application est ouverte. La TVA n'est pas calculée et les documents de vente restent des brouillons. Avant utilisation réelle, vérifier les fonctions sur une base fictive et mettre en place puis tester une sauvegarde indépendante, pièces jointes comprises.

## Préparation technique

1. Créer un projet Supabase **dédié** au cabinet. Ne pas modifier une base utilisée par un autre projet. Appliquer `schema.sql` à ce projet, dans une transaction, avec un outil administrateur de migrations. Le schéma `vetstock` est privé et aucune clé de base n'est fournie au navigateur. Appliquer une seule fois `provision_role.sql` : il crée le rôle PostgreSQL limité `vetstock_app` et conserve son mot de passe dans Supabase Vault. Ne pas recréer ce rôle si le service utilise déjà son mot de passe.
2. Vérifier que le projet et le service Render choisis restent dans les limites et plans gratuits souhaités. Leurs quotas et conditions peuvent changer.
3. Créer un service web Python Render depuis ce dépôt, après les tests réels sur PostgreSQL et vérification de sécurité. Commande de build : `pip install -r cloud/requirements.txt`. Commande de démarrage : `python -m cloud.server`. La sonde de santé est `/healthz`.
4. Dans l'environnement privé du service, définir :
   - `DATABASE_URL` : la chaîne de connexion PostgreSQL Supabase en mode transaction, port 6543, avec le rôle `vetstock_app.<référence-du-projet>` et son mot de passe provenant de Supabase Vault. Ne pas utiliser le rôle administrateur `postgres` pour l'application.
   - `VETSTOCK_DATABASE_CA_PATH` : le chemin du certificat CA Supabase disponible sur le serveur. La connexion impose `sslmode=verify-full` pour vérifier le certificat et le nom d'hôte. Voir les paramètres SSL de la base dans Supabase.
   - `VETSTOCK_ADMIN_USERNAME` et `VETSTOCK_ADMIN_PASSWORD` : les identifiants initiaux, uniquement avant la première création du compte ; mot de passe de 12 caractères minimum.
   - `VETSTOCK_PUBLIC_URL` est facultative sur Render : l'adresse `RENDER_EXTERNAL_URL` est utilisée par défaut. Si un domaine personnalisé est utilisé, définir explicitement son adresse HTTPS.
5. Après la première connexion réussie, supprimer le mot de passe initial de l'environnement Render et redéployer. Ne jamais envoyer de mots de passe ou de chaîne de connexion dans une discussion ni les enregistrer dans GitHub.

Ces instructions sont un point de départ, pas une attestation de disponibilité ou de sécurité en production. La migration, la connexion chiffrée, les permissions, les sauvegardes et le déploiement doivent être vérifiés dans les comptes réellement choisis.

## Vérifications

`python -m unittest cloud.test_cloud -v` teste la connexion, les rôles, le stock et les sessions avec des données fictives dans SQLite. `cloud.test_postgres` vérifie aussi les requêtes et les ventes simultanées sur PostgreSQL lorsqu'une base locale jetable `vetstock_test` est configurée pour les tests. Le workflow GitHub lance ces tests sur PostgreSQL ; il n'a pas accès aux secrets Supabase ou Render.
