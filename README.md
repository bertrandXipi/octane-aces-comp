# Compositing GPU temps réel Octane / F-Log2 ACES

Objectif : Créer une application locale de compositing temps réel sur GPU pour intégrer des passes de rendu EXR Octane sur des rushs vidéo Fujifilm F-Log2 dans un pipeline couleur ACES strict.

Périmètre & Fonctionnalités :
1. Pipeline couleur OCIO v2 : Ingestion et conversion en ACEScg (rushs F-Log2 -> ACEScg, passes Octane -> ACEScg), application finale de l'Output Transform ACES (vers sRGB/Rec.709) sur le viewport et l'export.
2. Chargement des médias : Lecture des passes multi-canaux EXR (Beauty, Diffuse, Specular/Reflection, Shadow, etc.) et décodage des masques Cryptomatte ; chargement du rush F-Log2.
3. Moteur de compositing GPU : Shaders temps réel (OpenGL / ModernGL ou WGPU) pour recomposition de la beauty, dosage fin des ombres et reflets isolés par masque Cryptomatte sur la vidéo de fond.
4. Interface utilisateur : UI locale (PySide6 / PyQt) avec prévisualisation viewport fluide (60 fps), sliders interactifs de réglages (balance reflets, densité d'ombres, gain, masque) et inspection A/B.
5. Persistance : Sauvegarde et rechargement des réglages dans un fichier JSON.
6. Rendu batch : Commande CLI batch pour rendre offscreen la séquence complète de 900 frames avec application des réglages JSON (export EXR 16-bit half ou ProRes/TIFF).

Stack recommandée : Python 3.11+, PySide6, PyOpenColorIO (OCIO), OpenImageIO / OpenEXR, ModernGL (ou Taichi / WGPU).

Definition of Done :
- L'application se lance localement et charge sans erreur un EXR multi-passes Octane et une frame F-Log2.
- La vue temps réel applique l'Output Transform ACES et réagit instantanément aux sliders de reflets/ombres avec masquage Cryptomatte.
- Les paramètres s'exportent et se réimportent fidèlement via JSON.
- La commande batch traite les 900 frames en rendu offscreen conforme à la prévisualisation.

_Projet créé depuis le cockpit le 2026-11-14._
