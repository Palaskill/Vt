# Éléments visuels VetStock

L'application sert les fichiers visuels depuis son propre serveur, sans CDN.
Elle n'a pas besoin de Node.js pour fonctionner. Les bibliothèques de cette
interface sont GSAP 3.15.0, Lenis 1.3.27 et React / React DOM 19.3.0.

La zone décorative utilise le composant officiel **SpotlightCard** de
[React Bits](https://github.com/DavidHDev/react-bits), dont le fichier source
et les conditions MIT + Commons Clause sont conservés dans ce dossier.
L'application utilise ce composant en tant que petit élément autonome ; les
formulaires et les opérations restent dans l'application JavaScript existante.
Les notices de licences de Lenis, React et React DOM sont également fournies.
Le fichier GSAP inclut sa notice et renvoie à sa licence officielle
<https://gsap.com/standard-license>.

Pour reconstruire uniquement le fichier précompilé `static/react-spotlight.js`
après modification de SpotlightCard ou de son point d'entrée :

```sh
cd design
npm install
npm run build
```

Les animations et le défilement lissé sont désactivés ou réduits selon la
préférence « réduire les animations ». Le défilement tactile reste natif.
