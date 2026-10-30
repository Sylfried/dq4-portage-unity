using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace DQ4
{
    /// <summary>Client TCP du pont (dq4_pc/pont.py). Un fil lit les lignes JSON ; le jeu les consomme dans Update().
    /// Se reconnecte tout seul : on peut lancer le pont avant ou apres Unity.</summary>
    public class PontClient
    {
        public readonly ConcurrentQueue<string> Recus = new ConcurrentQueue<string>();
        public volatile bool Connecte;

        readonly string hote;
        readonly int port;
        Thread fil;
        volatile bool arret;
        TcpClient tcp;
        NetworkStream flux;
        readonly object verrou = new object();

        public PontClient(string hote, int port)
        {
            this.hote = hote;
            this.port = port;
        }

        public void Demarrer()
        {
            fil = new Thread(Boucle);
            fil.IsBackground = true;
            fil.Name = "DQ4 pont";
            fil.Start();
        }

        public void Arreter()
        {
            arret = true;
            lock (verrou)
            {
                try { if (tcp != null) tcp.Close(); } catch (Exception) { }
            }
        }

        public void Envoyer(string ligne)
        {
            lock (verrou)
            {
                if (flux == null) return;
                try
                {
                    byte[] b = Encoding.UTF8.GetBytes(ligne + "\n");
                    flux.Write(b, 0, b.Length);
                }
                catch (Exception) { }
            }
        }

        void Boucle()
        {
            while (!arret)
            {
                try
                {
                    TcpClient c = new TcpClient();
                    c.NoDelay = true;
                    c.Connect(hote, port);
                    lock (verrou)
                    {
                        tcp = c;
                        flux = c.GetStream();
                    }
                    Connecte = true;
                    using (StreamReader lecteur = new StreamReader(flux, Encoding.UTF8))
                    {
                        string l;
                        while (!arret && (l = lecteur.ReadLine()) != null) Recus.Enqueue(l);
                    }
                }
                catch (Exception) { }
                Connecte = false;
                lock (verrou)
                {
                    try { if (tcp != null) tcp.Close(); } catch (Exception) { }
                    tcp = null;
                    flux = null;
                }
                if (!arret) Thread.Sleep(500);
            }
        }
    }
}
