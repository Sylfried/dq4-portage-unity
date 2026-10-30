using System;
using System.IO;
using UnityEngine;

namespace DQ4
{
    /// <summary>Recopie la console d'Unity dans un fichier texte (captures/unity/journal.txt, a la racine du depot) :
    /// les erreurs et avertissements peuvent ainsi etre lus sans copier-coller. Les captures d'ecran vont dans le meme dossier.</summary>
    public static class JournalDQ4
    {
        public static string Dossier;
        static string fichier;

        public static void Demarrer()
        {
            try
            {
                Dossier = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "captures", "unity"));
                Directory.CreateDirectory(Dossier);
                fichier = Path.Combine(Dossier, "journal.txt");
                File.WriteAllText(fichier, "Journal DQ4 - " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\n"
                    + "Unity " + Application.unityVersion + " | " + SystemInfo.graphicsDeviceName + " (" + SystemInfo.graphicsDeviceType + ")"
                    + " | " + SystemInfo.operatingSystem + "\n");
            }
            catch (Exception ex)
            {
                fichier = null;
                Debug.LogWarning("Journal DQ4 impossible : " + ex.Message);
            }
            Application.logMessageReceived -= Recu;
            Application.logMessageReceived += Recu;
        }

        public static void Arreter()
        {
            Application.logMessageReceived -= Recu;
        }

        static void Recu(string message, string pile, LogType type)
        {
            if (fichier == null) return;
            try
            {
                string ligne = DateTime.Now.ToString("HH:mm:ss.fff") + " [" + type + "] " + message + "\n";
                if (type == LogType.Exception || type == LogType.Error) ligne += pile + "\n";
                File.AppendAllText(fichier, ligne);
            }
            catch (Exception) { }
        }
    }
}
