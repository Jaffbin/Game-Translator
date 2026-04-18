import os
import json
import re
import shutil
import abc
import tkinter as tk
import requests
import time
from tkinter import filedialog, messagebox, scrolledtext
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# ==========================================
# 1. Core Interfaces (Abstract Base Classes)
# ==========================================

class ITranslationEngine:
    """Interface for translation engines (mock)."""
    def translate(self, text: str, target_lang: str) -> str:
        return f"Mock translation of {text} to {target_lang}"
    
class IGameHandler(abc.ABC):
    """游戏引擎处理接口，每个引擎都需要实现这些方法"""
    
    @staticmethod
    @abc.abstractmethod
    def get_engine_name() -> str:
        pass

    @staticmethod
    @abc.abstractmethod
    def detect(game_path: str) -> bool:
        """通过文件特征检测是否为该引擎游戏"""
        pass

    @abc.abstractmethod
    def find_text_files(self, game_path: str) -> List[str]:
        """返回需要翻译的文件路径列表"""
        pass

    @abc.abstractmethod
    def extract_text(self, file_path: str) -> List[Dict]:
        """提取文本，返回统一格式: [{'source': 'Hello', 'context': 'Map1'}, ...]"""
        pass

    @abc.abstractmethod
    def inject_text(self, file_path: str, translations: List[Dict], output_path: str):
        """将翻译后的文本回写到文件，保存到 output_path"""
        pass

# ==========================================
# 2. Concrete Implementations (Translators)
# ==========================================

class NvidiaTranslator(ITranslationEngine):
    MODEL = "meta/llama-3.1-8b-instruct"
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://integrate.api.nvidia.com/v1/chat/completions"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
    def _build_prompt(self, text: str, target_lang: str) -> str:
        # 优化 Prompt：极简 Prompt，移除"Text:"和"Rules:"这些导致AI困惑的前缀
        # 这样 AI 就不会把 JSON 的键（如 "name", "note"）和真正的文本搞混在一起。
        prompt = f"Text: {text}. Translate to {target_lang} only. Please only output the translated string without explanation."
        return prompt
    def translate(self, text: str, target_lang: str) -> str:
        if not text or not text.strip():
            return ""
        
        # 去�除首尾空字符，防止末尾多余的换行符引发解析错误
        text = text.strip()
        if len(text) == 0:
            return ""
        payload = {
            "model": self.MODEL,
            "messages": [
                {
                "role": "user",
                "content": self._build_prompt(text, target_lang)
            }
            ]
        }
        response = requests.post(self.url, json.dumps(payload), headers=self.headers).json()
        return response.json()["choices"][0]["message"]["content"]

# ==========================================
# 3. Concrete Implementations (Game Engines)
# ==========================================

class RPGMakerHandler(IGameHandler):
    """处理 RPG Maker MV/MZ - 修复版 (允许翻译武器/物品名称)"""

    # 1. 黑名单：只包含绝对不能翻译的字段（主要是ID、文件路径、参数）
    # 注意：移除了 'name', 'description', 'note'，否则武器/物品将无法翻译
    SKIP_KEYS = {
        'gameFont', 'font', # 字体
        'filename', 'file', # 文件名
        'faceset', 'picture', # 图片集
        'face', 'battlerName', 'characterName', # 角色立绘相关
        'system2', 'battleback1Name', 'battleback2Name', # 系统背景
        'title1Name', 'title2Name', 'parallaxName', 
        'panoramaName', 'bgmName', 'bgsName', 'meName', 'seName', # 音频
        'iconIndex', 'animationId', 'damage.type', 'damage.elementId', # ID类数据
        'code', 'indent', 'parameters', # 事件代码相关
        'gainSkill', 'addState', 'removeState', 'releaseState', # 技能ID引用
        'trait.code', 'trait.dataId', # 特性ID引用
        'action.skillId' # 动作技能ID
    }
    
    # 严禁翻译的文件后缀
    FORBIDDEN_EXTENSIONS = (
        '.ttf', '.otf', '.woff', '.woff2', 
        '.png', '.jpg', '.jpeg', '.svg',   
        '.mp4', '.webm',                   
        '.ogg', '.mp3', '.wav', '.m4a', 
        '.json', '.rpgmvp', '.rpgmvm', '.rpgmvo' # 游戏二进制文件
    )

    @staticmethod
    def get_engine_name() -> str:
        return "RPG Maker MV/MZ"

    @staticmethod
    def detect(game_path: str) -> bool:
        # 兼容 www/data 和直接在根目录的 data
        return os.path.exists(os.path.join(game_path, "www/data")) or \
               os.path.exists(os.path.join(game_path, "data"))

    def find_text_files(self, game_path: str) -> List[str]:
        # [修复] 确保 Weapons.json 在列表里
        data_path = os.path.join(game_path, "www/data") if os.path.exists(os.path.join(game_path, "www/data")) else os.path.join(game_path, "data")
        files = []
        for f in os.listdir(data_path):
            # 扫描所有 Map 文件和主要的数据库文件
            if f.startswith("Map") or f in [
                "CommonEvents.json", "System.json", "Actors.json", 
                "Items.json", "Weapons.json", "Armors.json", 
                "Skills.json", "States.json", "Enemies.json", 
                "Classes.json", "Troops.json", "MapInfos.json"
            ]:
                files.append(os.path.join(data_path, f))
        return files

    def extract_text(self, file_path: str) -> List[Dict]:
        texts = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            def scan_json(obj, context=''):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        
                        # [逻辑] 这里是关键判断
                        # 如果键名在黑名单（如 characterName），跳过
                        if k in self.SKIP_KEYS:
                            continue
                        
                        new_ctx = f"{context}.{k}" if context else k
                        scan_json(v, new_ctx)
                        
                elif isinstance(obj, list):
                    for item in obj:
                        scan_json(item, context)
                        
                elif isinstance(obj, str):
                    # 检查1：是否是文件后缀
                    if obj.lower().endswith(self.FORBIDDEN_EXTENSIONS):
                        return

                    # 检查2：看起来是纯变量/ID？
                    # 匹配 \v[1], \c[2], 纯数字
                    if re.match(r'^\d+$', obj) or re.match(r'^\\[vgnc]\[\d+\]$', obj):
                        return

                    # 检查3：看起来是文件名 (包含 . 但不含中文字符)
                    # 如果包含后缀名，或者路径符号 / \ ，且不含中文（判断 ASCII）， skip
                    # 注意：这里我们放宽了限制，允许翻译 "name" 字段中的英文文本
                    
                    # 长度检查
                    if len(obj) > 0:
                        # 如果全是ASCII字符且看起来像文件路径或代码，则跳过
                        # 比如 "mplus-1m-regular.woff" 会被这里拦截
                        if re.match(r'^[A-Za-z0-9_\-\.\:\/\\]+$', obj):
                             # 但是像 "Short Sword" (武器名) 即使是英文也要翻译
                             # 所以我们需要更智能一点：如果不含空格，通常是文件名；如果含空格，且不是系统路径，则翻译
                             if " " not in obj and "/" not in obj and "\\" not in obj:
                                 # 没有空格也没斜杠的英文，有可能是短代码或ID
                                 # 让我们通过文件名后缀再次判断
                                 if not obj.lower().endswith(self.FORBIDDEN_EXTENSIONS):
                                     pass # 可能是名字，不 return，继续往下走
                             else:
                                 # 带空格的不跳过（让它进入正则检查）
                                 pass
                        else:
                            # 包含非ASCII字符（即包含中文/日文） -> 需要翻译
                            pass

                        # 最终决定：加入待翻译列表
                        texts.append({'source': obj, 'context': context})
            
            scan_json(data)
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
        return texts

    def inject_text(self, file_path: str, translations: List[Dict], output_path: str):
        trans_map = {t['source']: t['target'] for t in translations}
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        for src, tgt in trans_map.items():
            # 这里做简单的替换
            content = content.replace(f'"{src}"', f'"{tgt}"')
            
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
class RenPyHandler(IGameHandler):
    """处理 Ren'Py (.rpy 文件)"""
    @staticmethod
    def get_engine_name() -> str:
        return "Ren'Py"

    @staticmethod
    def detect(game_path: str) -> bool:
        return os.path.exists(os.path.join(game_path, "game")) and \
               any(f.endswith('.rpy') for f in os.listdir(os.path.join(game_path, "game")))

    def find_text_files(self, game_path: str) -> List[str]:
        game_dir = os.path.join(game_path, "game")
        files = []
        for root, _, filenames in os.walk(game_dir):
            for filename in filenames:
                if filename.endswith('.rpy'):
                    files.append(os.path.join(root, filename))
        return files

    def extract_text(self, file_path: str) -> List[Dict]:
        texts = []
        # Ren'Py 经典正则: old "Text" new "Text" 或简单的 "Text" 作为 say 参数
        # 这里匹配最常见的 new "String" 模式
        pattern = re.compile(r'(?:old|new)\s+"((?:\\.|[^"\\])*)"')
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                matches = pattern.findall(line)
                for match in matches:
                    # 处理转义字符
                    clean_text = match.encode('utf-8').decode('unicode_escape')
                    texts.append({'source': clean_text, 'context': line.strip()[:20]})
        return texts

    def inject_text(self, file_path: str, translations: List[Dict], output_path: str):
        # Ren'Py 注入需要极其小心行号和格式
        # 这里做一个简单的映射演示
        trans_map = {t['source']: t['target'] for t in translations}
        
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        new_lines = []
        for line in lines:
            new_line = line
            # 简单查找替换（实际需要更复杂的 AST 解析或精确正则替换）
            for src, tgt in trans_map.items():
                if src in new_line:
                    new_line = new_line.replace(f'"{src}"', f'"{tgt}"')
                    break # 一行通常只翻译一句
            new_lines.append(new_line)
            
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

class UnityHandler(IGameHandler):
    """通用 Unity 资源扫描器 (针对不加密的 JSON/CSV/XML 资源)"""
    @staticmethod
    def get_engine_name() -> str:
        return "Unity Engine"

    @staticmethod
    def detect(game_path: str) -> bool:
        return os.path.exists(os.path.join(game_path, "GameAssembly.dll")) or \
               os.path.exists(os.path.join(game_path, "Data")) # IL2CPP 或 Mono

    def find_text_files(self, game_path: str) -> List[str]:
        # Unity 真正的难点在于 resources.assets 或 DLL。
        # 很多轻量级 Unity 游戏会使用 StreamingAssets 存放 JSON/CSV 表
        files = []
        potential_dirs = ["StreamingAssets", "Resources", "Data"]
        for d in potential_dirs:
            target_dir = os.path.join(game_path, d)
            if os.path.exists(target_dir):
                for root, _, filenames in os.walk(target_dir):
                    for filename in filenames:
                        if filename.endswith(('.json', '.csv', '.xml', '.txt')):
                            files.append(os.path.join(root, filename))
        return files

    def extract_text(self, file_path: str) -> List[Dict]:
        # 简单的文本扫描，假设 JSON 键值对
        texts = []
        try:
            if file_path.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # 扁平化扫描，类似 RPGMaker
                # ... (省略递归代码，同 RPGMaker)
            else:
                # 纯文本或 CSV 扫描
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if len(line) > 2:
                            texts.append({'source': line.strip(), 'context': 'RawText'})
        except:
            pass
        return texts

    def inject_text(self, file_path: str, translations: List[Dict], output_path: str):
        # 实现复制文件并尝试替换
        shutil.copy2(file_path, output_path)
        # 实际逻辑取决于文件类型，略

# ==========================================
# 4. Factory & Manager
# ==========================================

class GameSessionFactory:
    _handlers = [RPGMakerHandler, RenPyHandler, UnityHandler]

    @classmethod
    def detect_engine(cls, path: str) -> Optional[IGameHandler]:
        for Handler in cls._handlers:
            if Handler.detect(path):
                return Handler()
        return None

# ==========================================
# 5. User Interface (Tkinter)
# ==========================================

class TranslatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Auto Game Translator Tool")
        self.root.geometry("800x600")
        
        self.game_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.engine_handler = None
        
        self.setup_ui()

    def setup_ui(self):
        # Frame 1: Path Selection
        frame_top = tk.Frame(self.root, padx=10, pady=10)
        frame_top.pack(fill=tk.X)
        
        tk.Label(frame_top, text="Game Path:").pack(side=tk.LEFT)
        entry_path = tk.Entry(frame_top, textvariable=self.game_path, width=50)
        entry_path.pack(side=tk.LEFT, padx=5)
        
        tk.Button(frame_top, text="Browse/Exe", command=self.browse_game).pack(side=tk.LEFT)
        
        # Frame 2: Output
        frame_out = tk.Frame(self.root, padx=10, pady=5)
        frame_out.pack(fill=tk.X)
        tk.Button(frame_out, text="Start Translating", command=self.start_process, bg="lightblue").pack(side=tk.LEFT)
        
        # Frame 3: Log
        tk.Label(self.root, text="Processing Log:").pack(anchor=tk.W, padx=10)
        self.log_area = scrolledtext.ScrolledText(self.root, height=20)
        self.log_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    def log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.root.update()

    def browse_game(self):
        path = filedialog.askopenfilename(
            title="Select Game Executable",
            filetypes=[("Executable", "*.exe"), ("All Files", "*.*")]
        )
        if path:
            self.game_path.set(os.path.dirname(path)) # Get folder
            self.detect_game_engine()

    def detect_game_engine(self):
        path = self.game_path.get()
        self.log(f"Scanning path: {path} ...")
        self.engine_handler = GameSessionFactory.detect_engine(path)
        
        if self.engine_handler:
            self.log(f"[SUCCESS] Detected Engine: {self.engine_handler.get_engine_name()}")
        else:
            self.log("[WARNING] Unknown engine. Trying generic scanner...")

    def start_process(self):
        if not self.game_path.get():
            messagebox.showerror("Error", "Please select a game folder first.")
            return

        if not self.engine_handler:
            self.detect_game_engine()
            if not self.engine_handler:
                messagebox.showerror("Error", "Unsupported Engine.")
                return

        # 核心流程
        translator = NvidiaTranslator(api_key="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", model="meta/llama-3.1-8b-instruct") 
        
        files = self.engine_handler.find_text_files(self.game_path.get())
        
        self.log(f"Found {len(files)} target file(s).")
        
        game_root = self.game_path.get()
        output_root = os.path.join(game_root, "Translated_Patch") # 生成补丁文件夹
        os.makedirs(output_root, exist_ok=True)

        # 简单的文件映射逻辑
        for i, file_path in enumerate(files):
            self.log(f"Processing file {i+1}/{len(files)}: {os.path.basename(file_path)}")
            
            # 1. Extract
            texts = self.engine_handler.extract_text(file_path)
            self.log(f"  -> Extracted {len(texts)} strings.")
            
            # 2. Translate
            # 这里应该加一个去重逻辑，避免翻译重复句子浪费金钱
            translated_list = []
            for item in texts:
                res = translator.translate(item['source'], "Auto", "Chinese")
                translated_list.append({'source': item['source'], 'target': res})
            
            # 3. Inject
            # 保持相对路径结构
            rel_path = os.path.relpath(file_path, game_root)
            out_file_path = os.path.join(output_root, rel_path)
            
            self.engine_handler.inject_text(file_path, translated_list, out_file_path)
        
        self.log("="*30)
        self.log("Translation Complete! Patch created in: " + output_root)
        self.log("Please backup your game files before replacing.")

if __name__ == "__main__":
    root = tk.Tk()
    app = TranslatorApp(root)
    root.mainloop()