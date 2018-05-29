import atexit

import re
from argparse import ArgumentParser
from git import Git
from github import GithubException
from github.AuthenticatedUser import AuthenticatedUser
from github.Repository import Repository
from os import makedirs
from os.path import join, exists, isdir
from shutil import rmtree
from subprocess import call
from typing import Callable, Optional

from msk.console_action import ConsoleAction
from msk.exceptions import GithubRepoExists
from msk.lazy import Lazy
from msk.util import ask_input, to_camel, ask_yes_no, ask_for_github_credentials, ask_input_lines, \
    print_error

readme_template = '''## {title_name}
{short_description}

## Description
{long_description}

## Examples
{examples}

{credits}
'''

credits_template = '''## Credits
{author}

'''

init_template = '''from adapt.intent import IntentBuilder
from mycroft import MycroftSkill, intent_file_handler


class {class_name}(MycroftSkill):
    def __init__(self):
        MycroftSkill.__init__(self)

    @intent_file_handler('{intent_name}.intent')
    def handle_{handler_name}(self, message):
        self.speak_dialog('{intent_name}')


def create_skill():
    return {class_name}()

'''

gitignore_template = '''*.pyc
settings.json

'''

settingsmeta_template = '''{{
    "name": "{capital_desc}",
    "skillMetadata": {{
        "sections": [
            {{
                "name": "Options << Name of section",
                "fields": [
                    {{
                        "name": "internal_python_variable_name",
                        "type": "text",
                        "label": "Setting Friendly Display Name",
                        "value": "",
                        "placeholder": "demo prompt in the input box"
                    }}
                ]
            }},
            {{
                "name": "Login << Name of another section",
                "fields": [
                    {{
                        "type": "label",
                        "label": "Just a little bit of extra info for the user to understand following settings"
                    }},
                    {{
                        "name": "username",
                        "type": "text",
                        "label": "Username",
                        "value": ""
                    }},
                    {{
                        "name": "password",
                        "type": "password",
                        "label": "Password",
                        "value": ""
                    }}
                ]
            }}
        ]
    }}
}}'''


class CreateAction(ConsoleAction):
    def __init__(self, args, name: str = None, lang: str = None):
        if name:
            self.name = name
        if lang:
            self.lang = lang

    @staticmethod
    def register(parser: ArgumentParser):
        pass

    @Lazy
    def name(self) -> str:
        name_to_skill = {skill.name: skill for skill in self.msm.list()}
        while True:
            name = ask_input(
                'Enter a short unique skill name (ie. "siren alarm" or "pizza orderer"):',
                lambda x: re.match(r'^[a-zA-Z \-]+$', x), 'Please use only letter and spaces.'
            ).strip(' -').lower().replace(' ', '-')
            skill = name_to_skill.get(name, name_to_skill.get('{}-skill'.format(name)))
            if skill:
                print('The skill {} {}already exists'.format(
                    skill.name, 'by {} '.format(skill.author) * bool(skill.author)
                ))
                if ask_yes_no('Remove it? (y/N)', False):
                    rmtree(skill.path)
                else:
                    continue
            class_name = '{}Skill'.format(to_camel(name.replace('-', '_')))
            repo_name = '{}-skill'.format(name)
            print()
            print('Class name:', class_name)
            print('Repo name:', repo_name)
            print()
            alright = ask_yes_no('Looks good? (Y/n)', True)
            if alright:
                return name

    path = Lazy(lambda s: join(s.msm.skills_dir, s.name + '-skill'))
    git = Lazy(lambda s: Git(s.path))
    lang = Lazy(lambda s: ask_input(
        'Locale (default = en-us):', lambda x: re.match(r'^([a-z]{2}-[a-z]{2}|)$', x),
        'Please leave empty or write locale in format of xx-xx'
    ).lower() or 'en-us')
    short_description = Lazy(lambda s: ask_input(
        'Enter a one line description for your skill (ie. Orders fresh pizzas from the store):',
    ).capitalize())
    author = Lazy(lambda s: ask_input('Enter author:'))
    examples = Lazy(lambda s: [
        i.capitalize().rstrip('.') for i in ask_input_lines(
            'Enter some example phrases to trigger your skill:', '-'
        )
    ])
    long_description = Lazy(lambda s: '\n\n'.join(
        ask_input_lines('Enter a long description:', '>')
    ).strip().capitalize())
    readme = Lazy(lambda s: readme_template.format(
        title_name=s.name.replace('-', ' ').title(),
        short_description=s.short_description,
        long_description=s.long_description,
        examples=''.join(' - "{}"\n'.format(i) for i in s.examples),
        credits=credits_template.format(author=s.author)
    ))
    init_file = Lazy(lambda s: init_template.format(
        class_name=to_camel(s.name.replace('-', '_')),
        handler_name=s.intent_name.replace('.', '_'),
        intent_name=s.intent_name
    ))
    intent_name = Lazy(lambda s: '.'.join(reversed(s.name.split('-'))))

    def add_vocab(self):
        makedirs(join(self.path, 'vocab', self.lang))
        with open(join(self.path, 'vocab', self.lang, self.intent_name + '.intent'), 'w') as f:
            f.write(self.intent_name.replace('.', ' ').capitalize() + '\n\n')

    def add_dialog(self):
        makedirs(join(self.path, 'dialog', self.lang))
        with open(join(self.path, 'dialog', self.lang, self.intent_name + '.dialog'), 'w') as f:
            f.write(self.name.replace('-', ' ').capitalize() + '\n\n')

    def initialize_template(self, files: set = None):
        git = Git(self.path)

        skill_template = [
            ('', lambda: makedirs(self.path)),
            ('__init__.py', lambda: self.init_file),
            ('README.md', lambda: self.readme),
            ('vocab', self.add_vocab),
            ('dialog', self.add_dialog),
            ('.gitignore', lambda: gitignore_template),
            ('settingsmeta.json', lambda: settingsmeta_template.format(
                capital_desc=self.name.replace('-', ' ').capitalize()
            )),
            ('.git', lambda: git.init())
        ]

        def cleanup():
            rmtree(self.path)

        if not isdir(self.path):
            atexit.register(cleanup)
        for file, handler in skill_template:
            if files and file not in files:
                continue
            if not exists(join(self.path, file)):
                result = handler()
                if isinstance(result, str) and not exists(join(self.path, file)):
                    with open(join(self.path, file), 'w') as f:
                        f.write(result)
        atexit.unregister(cleanup)

    def commit_changes(self):
        if self.git.rev_parse('HEAD', with_exceptions=False) == 'HEAD':
            self.git.add('.')
            self.git.commit(message='Initial commit')

    def create_github_repo(self, get_repo_name: Callable = None) -> Optional[Repository]:
        if 'origin' not in Git(self.path).remote().split('\n'):
            if ask_yes_no('Would you like to create a GitHub repo for it? (Y/n)', True):
                user = ask_for_github_credentials().get_user()  # type: AuthenticatedUser
                repo_name = (get_repo_name and get_repo_name()) or (self.name + '-skill')
                try:
                    repo = user.create_repo(repo_name, self.short_description)
                except GithubException as e:
                    if e.status == 422:
                        raise GithubRepoExists(repo_name) from e
                    raise
                self.git.remote('add', 'origin', repo.html_url)
                call(['git', 'push', '-u', 'origin', 'master'], cwd=self.git.working_dir)
                print('Created GitHub repo:', repo.html_url)
                return repo
        return None

    def perform(self):
        self.initialize_template()
        self.commit_changes()
        with print_error(GithubRepoExists):
            self.create_github_repo()
        print('Created skill at:', self.path)