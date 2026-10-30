using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace DQ4.Editeur
{
    /// <summary>Menu "DQ4" de l'Editeur : lancer le jeu en un clic, ouvrir le dossier des captures.</summary>
    [InitializeOnLoad]
    public static class DQ4Menu
    {
        static DQ4Menu()
        {
            EditorApplication.delayCall += Initialiser;
        }

        static void Initialiser()
        {
            // les textures et les couleurs de sommet du jeu sont en gamma : pas de conversion lineaire
            if (PlayerSettings.colorSpace != ColorSpace.Gamma)
            {
                PlayerSettings.colorSpace = ColorSpace.Gamma;
                Debug.Log("DQ4 : espace colorimétrique réglé sur Gamma.");
            }
            if (!PlayerSettings.runInBackground) PlayerSettings.runInBackground = true;
        }

        [MenuItem("DQ4/Lancer le jeu (scène vide + Play)")]
        static void Lancer()
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            EditorApplication.isPlaying = true;
        }

        [MenuItem("DQ4/Ouvrir le dossier des captures")]
        static void OuvrirCaptures()
        {
            string d = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "captures", "unity"));
            Directory.CreateDirectory(d);
            EditorUtility.RevealInFinder(d);
        }
    }
}
