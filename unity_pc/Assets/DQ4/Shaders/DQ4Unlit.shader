// Shader maison du portage (piste 3) : texture x couleur de sommet x teinte, decoupe alpha, sans eclairage.
// Remplace les shaders du datapack (compiles pour Android : roses sous Windows).
// _Net = 1 : filtrage "bilineaire net" pour le pixel art (les pixels restent nets, seule la frontiere est adoucie), comme la visionneuse.
// _Rect : sous-rectangle de texture (u0 v0 u1 v1) pour les sprites ; (0,0,1,1) pour le decor.
// _ZTest = 5 (Greater) + _ZWrite = 0 : silhouette d'un personnage cache par le decor.
Shader "DQ4/Unlit"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Color ("Teinte", Color) = (1,1,1,1)
        _Cutoff ("Seuil alpha", Range(0,1)) = 0.5
        _Net ("Bilineaire net (0/1)", Float) = 0
        _Rect ("Rectangle UV (u0 v0 u1 v1)", Vector) = (0,0,1,1)
        [Enum(UnityEngine.Rendering.CompareFunction)] _ZTest ("ZTest", Float) = 4
        [Enum(Off,0,On,1)] _ZWrite ("ZWrite", Float) = 1
    }
    SubShader
    {
        Tags { "Queue"="AlphaTest" "RenderType"="TransparentCutout" "IgnoreProjector"="True" }
        LOD 100

        Pass
        {
            Cull Off
            Lighting Off
            ZWrite [_ZWrite]
            ZTest [_ZTest]
            Blend SrcAlpha OneMinusSrcAlpha

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 3.0
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float4 _MainTex_TexelSize;
            fixed4 _Color;
            fixed _Cutoff;
            float _Net;
            float4 _Rect;

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
                fixed4 color : COLOR;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                fixed4 col : COLOR0;
            };

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = v.uv;
                o.col = v.color;
                return o;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                float2 uv = lerp(_Rect.xy, _Rect.zw, i.uv);
                // bilineaire net, sans branchement (les derivees doivent etre calculees hors d'un if)
                float2 ts = _MainTex_TexelSize.zw;
                float2 p = uv * ts;
                float2 pc = floor(p) + 0.5;
                float2 w = max(fwidth(p), float2(1e-4, 1e-4));
                float2 uvNet = (pc + clamp((p - pc) / w, -0.5, 0.5)) / ts;
                uv = lerp(uv, uvNet, step(0.5, _Net));
                fixed4 c = tex2D(_MainTex, uv) * saturate(i.col) * _Color;
                clip(c.a - _Cutoff);
                return c;
            }
            ENDCG
        }
    }
}
