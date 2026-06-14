# Logos du bot (avatar selon le provider)

Au démarrage, le bot applique `assets/logo-<provider>.png` comme avatar Discord
si le fichier existe (sinon avatar inchangé). Fichiers attendus :

- `logo-anthropic.png` — affiché quand `PROVIDER=anthropic`
- `logo-mistral.png` — affiché quand `PROVIDER=mistral`

Contraintes : PNG (ou JPG/GIF), idéalement carré ≥ 128×128, < 256 Ko (limite Discord
avatar 256 Ko / 1 Mo selon format). L'édition d'avatar est globale au compte et
fortement rate-limitée : le bot ne ré-applique l'avatar que si le provider a changé
(marqueur `avatar-provider.marker` dans le volume d'état).

Ces fichiers sont commités dans le repo (baked dans l'image Docker). Si tu ne veux
pas les versionner, ajoute-les au `.gitignore` et monte-les en volume.
