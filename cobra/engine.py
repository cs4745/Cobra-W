# -*- coding: utf-8 -*-

"""
    engine
    ~~~~~~

    Implements scan engine

    :author:    Feei <feei@feei.cn>
    :homepage:  https://github.com/wufeifei/cobra
    :license:   MIT, see LICENSE for more details.
    :copyright: Copyright (c) 2017 Feei. All rights reserved
"""
import json
import os
import re
import traceback

import portalocker
from phply import phpast as php
from prettytable import PrettyTable

from cobra.core_engine.php.parser import scan_parser as php_scan_parser
from cobra.core_engine.php.engine import init_match_rule as php_init_match_rule
from rules.autorule import autorule
from . import const
from .cast import CAST
from .config import running_path
from .const import ext_dict
from .file import FileParseAll
from .log import logger
from .result import VulnerabilityResult
from .rule import Rule
from .utils import Tool


class Running:
    def __init__(self, sid):
        self.sid = sid

    def init_list(self, data=None):
        """
        Initialize asid_list file.
        :param data: list or a string
        :return:
        """
        file_path = os.path.join(running_path, '{sid}_list'.format(sid=self.sid))
        if not os.path.exists(file_path):
            if isinstance(data, list):
                with open(file_path, 'w') as f:
                    portalocker.lock(f, portalocker.LOCK_EX)
                    f.write(json.dumps({
                        'sids': {},
                        'total_target_num': len(data),
                    }))
            else:
                with open(file_path, 'w') as f:
                    portalocker.lock(f, portalocker.LOCK_EX)
                    f.write(json.dumps({
                        'sids': {},
                        'total_target_num': 1,
                    }))

    def list(self, data=None):
        file_path = os.path.join(running_path, '{sid}_list'.format(sid=self.sid))
        if data is None:
            with open(file_path, 'r') as f:
                portalocker.lock(f, portalocker.LOCK_EX)
                result = f.readline()
                return json.loads(result)
        else:
            with open(file_path, 'r+') as f:
                portalocker.lock(f, portalocker.LOCK_EX)
                result = f.read()
                if result == '':
                    result = {'sids': {}}
                else:
                    result = json.loads(result)
                result['sids'][data[0]] = data[1]
                f.seek(0)
                f.truncate()
                f.write(json.dumps(result))

    def status(self, data=None):
        file_path = os.path.join(running_path, '{sid}_status'.format(sid=self.sid))
        if data is None:
            with open(file_path) as f:
                portalocker.lock(f, portalocker.LOCK_EX)
                result = f.readline()
            return json.loads(result)
        else:
            data = json.dumps(data)
            with open(file_path, 'w') as f:
                portalocker.lock(f, portalocker.LOCK_EX)
                f.writelines(data)

    def data(self, data=None):

        file_path = os.path.abspath(running_path + '/{sid}_data'.format(sid=self.sid))

        if data is None:
            with open(file_path) as f:
                portalocker.lock(f, portalocker.LOCK_EX)
                result = f.readline()
            return json.loads(result)
        else:
            data = json.dumps(data, sort_keys=True)
            with open(file_path, 'w+') as f:
                portalocker.lock(f, portalocker.LOCK_EX)
                f.writelines(data)

    def is_file(self, is_data=False):
        if is_data:
            ext = 'data'
        else:
            ext = 'status'
        file_path = os.path.join(running_path, '{sid}_{ext}'.format(sid=self.sid, ext=ext))
        return os.path.isfile(file_path)


def score2level(score):
    level_score = {
        'CRITICAL': [9, 10],
        'HIGH': [6, 7, 8],
        'MEDIUM': [3, 4, 5],
        'LOW': [1, 2]
    }
    score = int(score)
    level = None
    for l in level_score:
        if score in level_score[l]:
            level = l
    if level is None:
        return 'Unknown'
    else:
        if score < 10:
            score_full = '0{s}'.format(s=score)
        else:
            score_full = score

        a = '{s}{e}'.format(s=score * '■', e=(10 - score) * '□')
        return '{l}-{s}: {ast}'.format(l=level[:1], s=score_full, ast=a)


def scan_single(target_directory, single_rule, files=None, language=None, secret_name=None):
    try:
        return SingleRule(target_directory, single_rule, files, language, secret_name).process()
    except Exception:
        raise


def scan(target_directory, a_sid=None, s_sid=None, special_rules=None, language=None, framework=None, file_count=0,
         extension_count=0, files=None, secret_name=None):
    r = Rule(language)
    vulnerabilities = r.vulnerabilities
    rules = r.rules(special_rules)
    find_vulnerabilities = []

    def store(result):
        if result is not None and isinstance(result, list) is True:
            for res in result:
                res.file_path = res.file_path.replace(target_directory, '')
                find_vulnerabilities.append(res)
        else:
            logger.debug('[SCAN] [STORE] Not found vulnerabilities on this rule!')

    if len(rules) == 0:
        logger.critical('no rules!')
        return False
    logger.info('[PUSH] {rc} Rules'.format(rc=len(rules)))
    push_rules = []

    for idx, single_rule in enumerate(sorted(rules.keys())):

        # init rule class
        r = getattr(rules[single_rule], single_rule)
        rule = r()

        if rule.status is False:
            logger.info('[CVI_{cvi}] [STATUS] OFF, CONTINUE...'.format(cvi=rule.svid))
            continue
        # SR(Single Rule)
        logger.debug("""[PUSH] [CVI_{cvi}] {idx}.{vulnerability}({language})""".format(
            cvi=rule.svid,
            idx=idx,
            vulnerability=rule.vulnerability,
            language=rule.language
        ))
        result = scan_single(target_directory, rule, files, language, secret_name)
        store(result)

    # print
    data = []
    data2 = []
    table = PrettyTable(
        ['#', 'CVI', 'Rule(ID/Name)', 'Lang/CVE-id', 'Target-File:Line-Number',
         'Commit(Author)', 'Source Code Content', 'Analysis'])

    table.align = 'l'
    trigger_rules = []
    for idx, x in enumerate(find_vulnerabilities):
        trigger = '{fp}:{ln}'.format(fp=x.file_path, ln=x.line_number)
        commit = u'@{author}'.format(author=x.commit_author)
        try:
            code_content = x.code_content[:50].strip()
        except AttributeError as e:
            code_content = x.code_content.decode('utf-8')[:100].strip()
        row = [idx + 1, x.id, x.rule_name, x.language, trigger, commit, code_content, x.analysis]
        row2 = [idx+1, x.chain]

        data.append(row)
        data2.append(row2)

        table.add_row(row)

        if x.id not in trigger_rules:
            logger.debug(' > trigger rule (CVI-{cvi})'.format(cvi=x.id))
            trigger_rules.append(x.id)

        # clear
        x.chain = ""

    diff_rules = list(set(push_rules) - set(trigger_rules))
    vn = len(find_vulnerabilities)
    if vn == 0:
        logger.info('[SCAN] Not found vulnerability!')
    else:
        logger.info("[SCAN] Trigger Rules: {tr} Vulnerabilities ({vn})\r\n{table}".format(tr=len(trigger_rules),
                                                                                          vn=len(find_vulnerabilities),
                                                                                          table=table))

        # 输出chain for all
        logger.info("[SCAN] Vulnerabilities Chain list: ")
        for d in data2:
            logger.info("[SCAN] Vul {}".format(d[0]))
            for c in d[1]:
                logger.info("[Chain] {}".format(c))

            if hasattr(os, 'get_terminal_size'):
                logger.info("[SCAN] ending\r\n" + '-'*(os.get_terminal_size().columns-16))
            else:
                logger.info("[SCAN] ending\r\n -------------------------------------------------------------------------")

        if len(diff_rules) > 0:
            logger.info(
                '[SCAN] Not Trigger Rules ({l}): {r}'.format(l=len(diff_rules), r=','.join(diff_rules)))

    # completed running data
    if s_sid is not None:
        Running(s_sid).data({
            'code': 1001,
            'msg': 'scan finished',
            'result': {
                'vulnerabilities': [x.__dict__ for x in find_vulnerabilities],
                'language': ",".join(language),
                'framework': framework,
                'extension': extension_count,
                'file': file_count,
                'push_rules': len(rules),
                'trigger_rules': len(trigger_rules),
                'target_directory': target_directory
            }
        })
    return True


class SingleRule(object):
    def __init__(self, target_directory, single_rule, files, language=None, secret_name=None):
        self.target_directory = target_directory
        self.find = Tool().find
        self.grep = Tool().grep
        self.sr = single_rule
        self.files = files
        self.languages = language
        self.lan = self.sr.language.lower()
        self.secret_name = secret_name
        # Single Rule Vulnerabilities
        """
        [
            vr
        ]
        """
        self.rule_vulnerabilities = []

        logger.info("[!] Start scan [CVI-{sr_id}]".format(sr_id=self.sr.svid))

    def origin_results(self):
        logger.debug('[ENGINE] [ORIGIN] match-mode {m}'.format(m=self.sr.match_mode))

        # grep
        if self.sr.match_mode == const.mm_regex_only_match:
            # 当所有match都满足时成立，当单一unmatch满足时，不成立
            matchs = self.sr.match
            unmatchs = self.sr.unmatch
            result = []
            new_result = []
            old_result = 0

            try:
                if matchs:
                    f = FileParseAll(self.files, self.target_directory, language=self.lan)

                    for match in matchs:

                        new_result = f.multi_grep(match)

                        if old_result == 0:
                            old_result = new_result
                            result = new_result
                            continue

                        old_result = result
                        result = []

                        for old_vul in old_result:
                            for new_vul in new_result:
                                if new_vul[0] == old_vul[0]:
                                    result.append(old_vul)

                    for unmatch in unmatchs:
                        uresults = f.multi_grep(unmatch)

                        for uresult in uresults:
                            for vul in result:
                                if vul[0] == uresult[0]:
                                    result.remove(vul)

                else:
                    result = None
            except Exception as e:
                traceback.print_exc()
                logger.debug('match exception ({e})'.format(e=e))
                return None

        elif self.sr.match_mode == const.mm_regex_param_controllable:
            # 自定义匹配，调用脚本中的匹配函数匹配参数
            match = self.sr.match

            try:
                if match:
                    f = FileParseAll(self.files, self.target_directory, language=self.lan)
                    result = f.grep(match)
                else:
                    result = None
            except Exception as e:
                traceback.print_exc()
                logger.debug('match exception ({e})'.format(e=e))
                return None

        elif self.sr.match_mode == const.mm_function_param_controllable:
            # 函数匹配，直接匹配敏感函数，然后处理敏感函数的参数即可
            # param controllable
            if '|' in self.sr.match:
                match = const.fpc_multi.replace('[f]', self.sr.match)
            else:
                match = const.fpc_single.replace('[f]', self.sr.match)

            try:
                if match:
                    f = FileParseAll(self.files, self.target_directory, language=self.lan)
                    result = f.grep(match)
                else:
                    result = None
            except Exception as e:
                traceback.print_exc()
                logger.debug('match exception ({e})'.format(e=e))
                return None

        elif self.sr.match_mode == const.mm_regex_return_regex:
            # 回馈式正则匹配，将匹配到的内容返回，然后合入正则表达式

            matchs = self.sr.match
            unmatchs = self.sr.unmatch
            matchs_name = self.sr.match_name
            black_list = self.sr.black_list

            result = []

            try:
                f = FileParseAll(self.files, self.target_directory, language=self.lan)

                result = f.multi_grep_name(matchs, unmatchs, matchs_name, black_list)
                if not result:
                    result = None
            except Exception as e:
                traceback.print_exc()
                logger.debug('match exception ({e})'.format(e=e))
                return None

        elif self.sr.match_mode == const.sp_crx_keyword_match:
            # 针对crx研究的keyword匹配，先以sp crx作为入口，逐渐思考普适性

            keyword = self.sr.keyword
            match = self.sr.match
            unmatch = self.sr.unmatch

            result = []

            try:
                f = FileParseAll(self.files, self.target_directory, language=self.lan)

                result = f.special_crx_keyword_match(keyword, match, unmatch)
                if not result:
                    result = None
            except Exception as e:
                traceback.print_exc()
                logger.debug('match exception ({e})'.format(e=e))
                return None

        else:
            logger.warning('Exception match mode: {m}'.format(m=self.sr.match_mode))
            result = None

        try:
            result = result.decode('utf-8')
        except AttributeError as e:
            pass

        return result

    def process(self):
        """
        Process Single Rule
        :return: SRV(Single Rule Vulnerabilities)
        """
        origin_results = self.origin_results()
        # exists result
        if origin_results == '' or origin_results is None:
            logger.debug('[CVI-{cvi}] [ORIGIN] NOT FOUND!'.format(cvi=self.sr.svid))
            return None

        origin_vulnerabilities = origin_results
        for index, origin_vulnerability in enumerate(origin_vulnerabilities):
            logger.debug(
                '[CVI-{cvi}] [ORIGIN] {line}'.format(cvi=self.sr.svid, line=": ".join(list(origin_vulnerability))))
            if origin_vulnerability == ():
                logger.debug(' > continue...')
                continue
            vulnerability = self.parse_match(origin_vulnerability)
            if vulnerability is None:
                logger.debug('Not vulnerability, continue...')
                continue
            is_test = False
            try:
                datas = Core(self.target_directory, vulnerability, self.sr, 'project name',
                             ['whitelist1', 'whitelist2'], test=is_test, index=index,
                             files=self.files, languages=self.languages, secret_name=self.secret_name).scan()
                data = ""

                if len(datas) == 3:
                    is_vulnerability, reason, data = datas

                    if "New Core" not in reason:
                        code = "Code: {}".format(origin_vulnerability[2].strip(" "))
                        file_path = os.path.normpath(origin_vulnerability[0])
                        data.insert(1, ("NewScan", code, origin_vulnerability[0], origin_vulnerability[1]))

                elif len(datas) == 2:
                    is_vulnerability, reason = datas
                else:
                    is_vulnerability, reason = False, "Unpack error"

                if is_vulnerability:
                    logger.debug('[CVI-{cvi}] [RET] Found {code}'.format(cvi=self.sr.svid, code=reason))
                    vulnerability.analysis = reason
                    vulnerability.chain = data
                    self.rule_vulnerabilities.append(vulnerability)
                else:
                    if reason == 'New Core':  # 新的规则
                        logger.debug('[CVI-{cvi}] [NEW-VUL] New Rules init')
                        new_rule_vulnerabilities = NewCore(self.sr, self.target_directory, data, self.files, 0, languages=self.languages, secret_name=self.secret_name)

                        if len(new_rule_vulnerabilities) > 0:
                            self.rule_vulnerabilities.extend(new_rule_vulnerabilities)

                    else:
                        logger.debug('Not vulnerability: {code}'.format(code=reason))
            except Exception:
                raise
        logger.debug('[CVI-{cvi}] {vn} Vulnerabilities: {count}'.format(cvi=self.sr.svid, vn=self.sr.vulnerability,
                                                                        count=len(self.rule_vulnerabilities)))
        return self.rule_vulnerabilities

    def parse_match(self, single_match):
        mr = VulnerabilityResult()
        # grep result
        #
        # Rules
        #
        # (u'D:\\program\\cobra-w\\tests\\vulnerabilities/v.php', 10, 'echo($callback . ";");\n')
        try:
            mr.line_number = single_match[1]
            mr.code_content = single_match[2]
            mr.file_path = single_match[0]
        except Exception:
            logger.warning('[ENGINE] match line parse exception')
            mr.file_path = ''
            mr.code_content = ''
            mr.line_number = 0

        # vulnerability information
        mr.rule_name = self.sr.vulnerability
        mr.id = self.sr.svid
        mr.language = self.sr.language
        mr.commit_author = self.sr.author

        return mr


class Core(object):
    def __init__(self, target_directory, vulnerability_result, single_rule, project_name, white_list, test=False,
                 index=0, files=None, languages=None, secret_name=None):
        """
        Initialize
        :param: target_directory:
        :param: vulnerability_result:
        :param single_rule: rule class
        :param project_name: project name
        :param white_list: white-list
        :param test: is test
        :param index: vulnerability index
        :param files: core file list
        :param secret_name: secret name
        """
        self.data = []
        self.repair_dict = {}
        self.repair_functions = []
        self.controlled_list = {}

        self.target_directory = target_directory

        self.file_path = vulnerability_result.file_path.strip()
        self.line_number = vulnerability_result.line_number
        # self.code_content = vulnerability_result.code_content.strip()
        self.code_content = vulnerability_result.code_content
        self.files = files
        self.languages = languages
        self.secret_name = secret_name

        self.rule_match = single_rule.match
        self.rule_match_mode = single_rule.match_mode
        self.vul_function = single_rule.vul_function
        self.cvi = single_rule.svid
        self.lan = single_rule.language.lower()
        self.single_rule = single_rule

        self.project_name = project_name
        self.white_list = white_list
        self.test = test

        self.status = None
        self.status_init = 0
        self.status_fixed = 2

        # const.py
        self.repair_code = None
        self.repair_code_init = 0
        self.repair_code_fixed = 1
        self.repair_code_not_exist_file = 4000
        self.repair_code_special_file = 4001
        self.repair_code_whitelist = 4002
        self.repair_code_test_file = 4003
        self.repair_code_annotation = 4004
        self.repair_code_modify = 4005
        self.repair_code_empty_code = 4006
        self.repair_code_const_file = 4007
        self.repair_code_third_party = 4008

        self.method = None
        logger.debug("""[CVI-{cvi}] [VERIFY-VULNERABILITY] ({index})
        > File: `{file}:{line}`
        > Code: `{code}`""".format(
            cvi=single_rule.svid,
            index=index,
            file=self.file_path.replace(self.target_directory, ''),
            line=self.line_number,
            code=self.code_content))

    def is_white_list(self):
        """
        Is white-list file
        :return: boolean
        """
        return self.file_path.split(self.target_directory, 1)[1] in self.white_list

    def is_special_file(self):
        """
        Is special file
        :method: According to the file name to determine whether the special file
        :return: boolean
        """
        special_paths = [
            '/node_modules/',
            '/bower_components/',
            '.min.js',
        ]
        for path in special_paths:
            if path in self.file_path:
                return True
        return False

    def is_test_file(self):
        """
        Is test case file
        :method: file name
        :return: boolean
        """
        test_paths = [
            '/test/',
            '/tests/',
            '/unitTests/'
        ]
        for path in test_paths:
            if path in self.file_path:
                return True
        return False

    def is_match_only_rule(self):
        """
        Whether only match the rules, do not parameter controllable processing
        :method: It is determined by judging whether the left and right sides of the regex_location are brackets
        :return: boolean
        """
        if self.rule_match_mode == 'regex-only-match':
            return True
        else:
            return False

    def is_annotation(self):
        """
        Is annotation
        :method: Judgment by matching comment symbols (skipped when self.is_match_only_rule condition is met)
               - PHP:  `#` `//` `\*` `*`
                    //asdfasdf
                    \*asdfasdf
                    #asdfasdf
                    *asdfasdf
               - Java:
        :return: boolean
        """
        match_result = re.findall(r"(#|\\\*|\/\/)+", self.code_content)
        # Skip detection only on match
        if self.is_match_only_rule():
            return False
        else:
            return len(match_result) > 0

    def is_can_parse(self):
        """
        Whether to parse the parameter is controllable operation
        :return:
        """
        for language in CAST.languages:
            if self.file_path[-len(language):].lower() == language:
                return True
        return False

    def is_target(self):
        """
        try to find ext for target file and check it wheater target or not 
        :return: 
        """
        # get ext for file
        fileext = "." + self.file_path.split(".")[-1]

        if self.lan in ext_dict and fileext is not None:
            if fileext in ext_dict[self.lan]:
                return True

        return False

    def init_php_repair(self):
        """
        初始化修复函数规则
        :return: 
        """
        # self.single_rule.svid
        a = __import__('rules.secret.demo', fromlist=['IS_REPAIR_DEFAULT'])
        self.repair_dict = getattr(a, 'IS_REPAIR_DEFAULT')

        b = __import__('rules.secret.demo', fromlist=['IS_CONTROLLED_DEFAULT'])
        self.controlled_list = getattr(b, 'IS_CONTROLLED_DEFAULT')

        if self.secret_name is not None:
            try:
                # 首先加载修复函数指定
                a = __import__('rules.secret.' + self.secret_name, fromlist=[self.secret_name])
                a = getattr(a, self.secret_name)
                self.repair_dict = self.repair_dict.copy()
                self.repair_dict.update(a.items())

                # 然后加载输入函数
                b = __import__('rules.secret.' + self.secret_name, fromlist=[self.secret_name])
                b = getattr(b, self.secret_name + "_controlled")
                self.controlled_list += b

            except ImportError:
                logger.warning('[AST][INIT] Secret_name init error... No module named {}'.format(self.secret_name))

        # init
        for key in self.repair_dict:
            if self.single_rule.svid in self.repair_dict[key]:
                self.repair_functions.append(key)

    def scan(self):
        """
        Scan vulnerabilities
        :flow:
        - whitelist file
        - special file
        - test file
        - annotation
        - rule
        :return: is_vulnerability, code
        """
        self.method = 0
        self.code_content = self.code_content
        if len(self.code_content) > 512:
            self.code_content = self.code_content[:500]
        self.status = self.status_init
        self.repair_code = self.repair_code_init
        if self.is_white_list():
            logger.debug("[RET] Whitelist")
            return False, 'Whitelists(白名单)'

        if self.is_special_file():
            logger.debug("[RET] Special File")
            return False, 'Special File(特殊文件)'

        if self.is_test_file():
            logger.debug("[CORE] Test File")

        if self.is_annotation():
            logger.debug("[RET] Annotation")
            return False, 'Annotation(注释)'

        if not self.is_target():
            logger.error("[SCAN] file {} ext is not support, something error...".format(self.file_path))
            return False, 'Unsupport File'

        #
        # function-param-regex
        # Match(function) -> Param-Controllable -> Repair -> Done
        #

        #
        # vustomize-match
        # Match(function) -> vustomize-match() -> Param-Controllable -> Repair -> Done
        #
        logger.debug('[CVI-{cvi}] match-mode {mm}'.format(cvi=self.cvi, mm=self.rule_match_mode))
        # if self.file_path[-3:].lower() == 'php':
        if self.lan == "php":
            try:
                self.init_php_repair()
                ast = CAST(self.rule_match, self.target_directory, self.file_path, self.line_number,
                           self.code_content, files=self.files, rule_class=self.single_rule, repair_functions=self.repair_functions, controlled_params=self.controlled_list)

                # only match
                if self.rule_match_mode == const.mm_regex_only_match:
                    #
                    # Regex-Only-Match
                    # Match(regex) -> Repair -> Done
                    #
                    logger.debug("[CVI-{cvi}] [ONLY-MATCH]".format(cvi=self.cvi))
                    return True, 'Regex-only-match'

                # Match for function-param-regex
                if self.rule_match_mode == const.mm_function_param_controllable:
                    rule_match = self.rule_match.strip('()').split('|')
                    logger.debug('[RULE_MATCH] {r}'.format(r=rule_match))
                    try:
                        # with open(self.file_path, 'r') as fi:
                        # fi = codecs.open(self.file_path, "r", encoding='utf-8', errors='ignore')
                        # code_contents = fi.read()
                        result = php_scan_parser(rule_match, self.line_number, self.file_path, repair_functions=self.repair_functions, controlled_params=self.controlled_list)
                        logger.debug('[AST] [RET] {c}'.format(c=result))
                        if len(result) > 0:
                            if result[0]['code'] == 1:  # 函数参数可控
                                return True, 'Function-param-controllable', result[0]['chain']

                            elif result[0]['code'] == 2:  # 漏洞修复
                                return False, 'Function-param-controllable but fixed', result[0]['chain']

                            elif result[0]['code'] == 3:  # 疑似漏洞
                                return True, 'Unconfirmed Function-param-controllable', result[0]['chain']

                            elif result[0]['code'] == -1:  # 函数参数不可控
                                return False, 'Function-param-uncon', result[0]['chain']

                            elif result[0]['code'] == 4:  # 新规则生成
                                return False, 'New Core', result[0]['source']

                            logger.debug('[AST] [CODE] {code}'.format(code=result[0]['code']))
                        else:
                            logger.debug(
                                '[AST] Parser failed / vulnerability parameter is not controllable {r}'.format(
                                    r=result))
                            return False, 'Can\'t parser'
                    except Exception:
                        exc_msg = traceback.format_exc()
                        logger.warning(exc_msg)
                        raise

                # vustomize-match
                param_is_controllable, code, data, chain = ast.is_controllable_param()
                if param_is_controllable:
                    logger.debug('[CVI-{cvi}] [PARAM-CONTROLLABLE] Param is controllable'.format(cvi=self.cvi))

                    if code == 1:
                        return True, 'Vustomize-Match', chain
                    elif code ==3:
                        return False, 'Unconfirmed Vustomize-Match', chain

                else:
                    if type(data) is tuple:
                        if int(data[0]) == 4:
                            return False, 'New Core', data[1]

                    logger.debug('[CVI-{cvi}] [PARAM-CONTROLLABLE] Param Not Controllable'.format(cvi=self.cvi))
                    return False, 'Param-Not-Controllable'
            except Exception as e:
                logger.debug(traceback.format_exc())
                return False, 'Exception'

        # elif self.file_path[-3:].lower() == 'sol':
        elif self.lan == "solidity":
            try:
                ast = CAST(self.rule_match, self.target_directory, self.file_path, self.line_number,
                           self.code_content, files=self.files, rule_class=self.single_rule,
                           repair_functions=self.repair_functions)

                # only match
                if self.rule_match_mode == const.mm_regex_only_match:
                    #
                    # Regex-Only-Match
                    # Match(regex) -> Repair -> Done
                    #
                    logger.debug("[CVI-{cvi}] [ONLY-MATCH]".format(cvi=self.cvi))
                    return True, 'Regex-only-match'
                elif self.rule_match_mode == const.mm_regex_return_regex:
                    logger.debug("[CVI-{cvi}] [REGEX-RETURN-REGEX]".format(cvi=self.cvi))
                    return True, 'Regex-return-regex'
                else:
                    logger.warn("[CVI-{cvi} [OTHER-MATCH]] sol ruls only support for Regex-only-match and Regex-return-regex...".format(cvi=self.cvi))
                    return False, 'Unsupport Match'

            except Exception as e:
                logger.debug(traceback.format_exc())
                return False, 'Exception'

        # elif self.file_path[-3:].lower() == '.js':
        elif self.lan == "javascript":
            try:
                ast = CAST(self.rule_match, self.target_directory, self.file_path, self.line_number,
                           self.code_content, files=self.files, rule_class=self.single_rule,
                           repair_functions=self.repair_functions)

                # only match
                if self.rule_match_mode == const.mm_regex_only_match:
                    #
                    # Regex-Only-Match
                    # Match(regex) -> Repair -> Done
                    #
                    logger.debug("[CVI-{cvi}] [ONLY-MATCH]".format(cvi=self.cvi))
                    return True, 'Regex-only-match'
                elif self.rule_match_mode == const.mm_regex_return_regex:
                    logger.debug("[CVI-{cvi}] [REGEX-RETURN-REGEX]".format(cvi=self.cvi))
                    return True, 'Regex-return-regex'
                else:
                    logger.warn("[CVI-{cvi} [OTHER-MATCH]] sol ruls only support for Regex-only-match and Regex-return-regex...".format(cvi=self.cvi))
                    return False, 'Unsupport Match'

            except Exception as e:
                logger.debug(traceback.format_exc())
                return False, 'Exception'

        elif self.lan == "chromeext":
            try:
                ast = CAST(self.rule_match, self.target_directory, self.file_path, self.line_number,
                           self.code_content, files=self.files, rule_class=self.single_rule,
                           repair_functions=self.repair_functions)

                # only match
                if self.rule_match_mode == const.mm_regex_only_match:
                    #
                    # Regex-Only-Match
                    # Match(regex) -> Repair -> Done
                    #
                    logger.debug("[CVI-{cvi}] [ONLY-MATCH]".format(cvi=self.cvi))
                    return True, 'Regex-only-match'
                elif self.rule_match_mode == const.mm_regex_return_regex:
                    logger.debug("[CVI-{cvi}] [REGEX-RETURN-REGEX]".format(cvi=self.cvi))
                    return True, 'Regex-return-regex'
                elif self.rule_match_mode == const.sp_crx_keyword_match:
                    logger.debug("[CVI-{cvi}] [SPECIAL-CRX-KEYWORD-MATCH]".format(cvi=self.cvi))
                    return True, 'Specail-crx-keyword-match'
                else:
                    logger.warn("[CVI-{cvi} [OTHER-MATCH]] sol ruls only support for Regex-only-match and Regex-return-regex...".format(cvi=self.cvi))
                    return False, 'Unsupport Match'

            except Exception as e:
                logger.debug(traceback.format_exc())
                return False, 'Exception'


def init_match_rule(data, lan='php'):
    """
    处理新生成规则初始化正则匹配
    :param lan: 
    :param data: 
    :return: 
    """
    if lan.lower() == "php":
        return php_init_match_rule(data)


def auto_parse_match(single_match, svid, language):
    mr = VulnerabilityResult()
    # grep result
    #
    # Rules
    #
    # (u'D:\\program\\cobra-w\\tests\\vulnerabilities/v.php', 10, 'echo($callback . ";");\n')
    try:
        mr.line_number = single_match[1]
        mr.code_content = single_match[2]
        mr.file_path = single_match[0]
    except Exception:
        logger.warning('match line parse exception')
        mr.file_path = ''
        mr.code_content = ''
        mr.line_number = 0

    # vulnerability information
    mr.rule_name = 'Auto rule'
    mr.id = svid
    mr.language = language
    mr.commit_author = 'Cobra-W'

    return mr


def NewCore(old_single_rule, target_directory, new_rules, files, count=0, languages=None, secret_name=None):
    """
    处理新的规则生成
    :param languages: 
    :param old_single_rule: 
    :param secret_name: 
    :param target_directory: 
    :param new_rules: 
    :param files: 
    :param count:
    :return: 
    """
    count += 1

    if count > 20:
        logger.warning("[New Rule] depth too big to auto exit...")
        return False

    # init
    match_mode = "New rule to Vustomize-Match"
    logger.debug('[ENGINE] [ORIGIN] match-mode {m}'.format(m=match_mode))

    match, match2, vul_function, index = init_match_rule(new_rules, lan=old_single_rule.language)
    logger.debug('[ENGINE] [New Rule] new match_rule: {}'.format(match))

    sr = autorule()
    sr.match = match
    sr.vul_function = vul_function

    # 从旧的规则类中读取部分数据
    svid = old_single_rule.svid
    language = old_single_rule.language
    sr.svid = svid
    sr.language = language

    # grep

    try:
        if match:
            f = FileParseAll(files, target_directory)
            result = f.grep(match)
        else:
            result = None
    except Exception as e:
        traceback.print_exc()
        logger.debug('match exception ({e})'.format(e=e))
        return None
    try:
        result = result.decode('utf-8')
    except AttributeError as e:
        pass

    # 进入分析
    origin_vulnerabilities = result
    rule_vulnerabilities = []

    for index, origin_vulnerability in enumerate(origin_vulnerabilities):

        code = origin_vulnerability[2]
        if match2 is not None:
            if re.search(match2, code, re.I):
                continue

        logger.debug(
            '[CVI-{cvi}] [ORIGIN] {line}'.format(cvi=svid, line=": ".join(list(origin_vulnerability))))
        if origin_vulnerability == ():
            logger.debug(' > continue...')
            continue
        vulnerability = auto_parse_match(origin_vulnerability, svid, language)
        if vulnerability is None:
            logger.debug('Not vulnerability, continue...')
            continue

        try:
            datas = Core(target_directory, vulnerability, sr, 'project name',
                         ['whitelist1', 'whitelist2'], files=files, secret_name=secret_name).scan()
            data = ""

            if len(datas) == 3:
                is_vulnerability, reason, data = datas

                if "New Core" not in reason:
                    code = "Code: {}".format(origin_vulnerability[2])
                    data.insert(1, ("NewScan", code, origin_vulnerability[0], origin_vulnerability[1]))

            elif len(datas) == 2:
                is_vulnerability, reason = datas
            else:
                is_vulnerability, reason = False, "Unpack error"

            if is_vulnerability:
                logger.debug('[CVI-{cvi}] [RET] Found {code}'.format(cvi="00000", code=reason))
                vulnerability.analysis = reason
                vulnerability.chain = data
                rule_vulnerabilities.append(vulnerability)
            else:
                if reason == 'New Core':  # 新的规则
                    logger.debug('[CVI-{cvi}] [NEW-VUL] New Rules init')
                    new_rule_vulnerabilities = NewCore(sr, target_directory, data, files, count, secret_name=secret_name)

                    if not new_rule_vulnerabilities:
                        return rule_vulnerabilities

                    if len(new_rule_vulnerabilities) > 0:
                        rule_vulnerabilities.extend(new_rule_vulnerabilities)

                else:
                    logger.debug('Not vulnerability: {code}'.format(code=reason))

        except Exception:
            raise

    return rule_vulnerabilities
