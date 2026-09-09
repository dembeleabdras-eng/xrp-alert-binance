# XRP Alert Binance — version simple

Projet personnel : surveille XRP/USDT Perpetual sur Binance Futures et envoie une notification Push Android lorsqu'une cible est atteinte.

## Déploiement
Commande de démarrage recommandée :
gunicorn app:app

Le service doit être accessible en HTTPS pour les notifications Push.

## Important
- Aucune clé API Binance n'est nécessaire.
- L'application ne passe aucun ordre.
- Les alertes et abonnements sont conservés dans SQLite.
- Pour un service vraiment 24/7, utiliser un hébergement qui ne met pas le service en veille.
- Le fichier `xrp_alert.db` doit être sur un stockage persistant si l'hébergeur rend le disque éphémère.
